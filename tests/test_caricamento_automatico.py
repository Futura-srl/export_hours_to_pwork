"""Test del caricamento automatico delle ore su Pwork e della chiusura del mese.

La data di oggi e' fissata al 17/09/2030 (lontano dai dati reali e da quelli di prova) e l'ultimo mese chiuso a luglio 2030: i giorni da caricare
vanno dal 01/08/2030 a ieri. La spunta "Carica ore su Pwork" e' messa dal test su un tipo esistente e nel
database non ci sono viaggi nel periodo del test, quindi i dati reali non entrano nel caricamento.

Nessun test esce verso Pwork: ogni chiamata HTTP fatta con `requests` fa fallire il test, e dove serve
un invio `send_timesheet` e `get_token_from_pwork` sono sostituiti. I test girano in transazione.
"""

from datetime import date, datetime
from unittest.mock import patch

import pytz
import requests

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

TZ = pytz.timezone('Europe/Rome')
OGGI = date(2030, 9, 17)


def _http_vietato(*args, **kwargs):
    raise AssertionError(
        "Il test ha tentato una chiamata HTTP (Pwork o altro): nei test non si invia nulla."
    )


def _roma(anno, mese, giorno, ora, minuti=0):
    """ Orario italiano -> datetime UTC senza fuso, come lo salva Odoo """
    return TZ.localize(datetime(anno, mese, giorno, ora, minuti)).astimezone(pytz.utc).replace(tzinfo=None)


@tagged('post_install', '-at_install')
class TestCaricamentoAutomatico(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        blocco = patch('requests.sessions.Session.request', _http_vietato)
        blocco.start()
        cls.addClassCleanup(blocco.stop)

        oggi = patch('odoo.addons.export_hours_to_pwork.models.pwork_caricamento.PworkCaricamento._oggi',
                     lambda self: OGGI)
        oggi.start()
        cls.addClassCleanup(oggi.stop)

        # l'automazione sugli allievi va eliminata in produzione: nei test non deve esistere
        cls.env['base.automation'].search(
            [('name', '=', 'Controllo timesheet generati secondo autista')]).write({'active': False})

        cls.parametri = cls.env['ir.config_parameter'].sudo()
        cls.caricamento = cls.env['pwork.caricamento']
        cls.caricamento._imposta_mese_chiuso(date(2030, 7, 1))
        for nome in ('caricamento_automatico', 'chiusura_automatica', 'giorno_scadenza_chiusura',
                     'email_avvisi', 'email_hr', 'ultimo_rapporto', 'ultimo_controllo_hr', 'metodo_precedente'):
            cls.parametri.set_param('export_hours_to_pwork.%s' % nome, False)

        # due tipi esistenti (la copia non regge i dati reali): la spunta vale solo dentro la transazione;
        # nel database non ci sono viaggi nel periodo del test
        cls.tipo, cls.tipo_senza_spunta = cls.env['gtms.trip.type'].search(
            [('task_id', '!=', False), ('task_id.project_id', '!=', False)], limit=2)
        cls.env['gtms.trip.type'].search([('carica_ore_pwork', '=', True)]).write({'carica_ore_pwork': False})
        cls.tipo.carica_ore_pwork = True
        cls.veicolo = cls.env['fleet.vehicle'].search([], limit=1)
        # righe automatiche gia' presenti nel database (es. prove a mano): fuori dai test di invio
        cls.env['account.analytic.line.pwork'].search(
            [('invio_automatico', '=', True), ('pwork', '=', False), ('error_txt', '=', False)]).write({'invio_automatico': False})

        cls.autista, cls.dipendente, cls.contratto = cls._crea_autista('Uno', 'Caricamento')
        cls.secondo_autista, cls.secondo_dipendente, _contratto = cls._crea_autista('Due', 'Caricamento')

    @classmethod
    def _crea_autista(cls, nome, cognome):
        partner = cls.env['res.partner'].create({
            'name': '%s %s' % (nome, cognome), 'first_name': nome, 'last_name': cognome})
        dipendente = cls.env['hr.employee'].create({
            'name': '%s %s' % (nome, cognome), 'first_name': nome, 'last_name': cognome,
            'address_home_id': partner.id,
        })
        contratto = cls.env['hr.contract'].create({
            'name': 'Contratto test %s %s' % (nome, cognome),
            'employee_id': dipendente.id,
            'date_start': date(2025, 1, 1),
            'wage': 1,
            'state': 'open',
        })
        return partner, dipendente, contratto

    # ------------------------------------------------------------------
    # dati
    # ------------------------------------------------------------------
    def _viaggio(self, nome, inizio, fine, stato='checked', tipo=None, autista=None, pagamento='ore_pianificate', allievo=None, **valori):
        viaggio = self.env['gtms.trip'].create(dict({
            'name': nome,
            'trip_type_id': (tipo or self.tipo).id,
            'first_stop_planned_at': inizio,
            'last_stop_planned_at': fine,
            'drivers_payment': pagamento,
        }, **valori))
        if autista is not False:
            self.env['gtms.trip.vehicle.manager'].create({
                'trip_id': viaggio.id,
                'driver_id': (autista or self.autista).id,
                'learning_driver_id': allievo.id if allievo else False,
                'fleet_vehicle_id': self.veicolo.id,
            })
        if stato:
            # `state` e' calcolato e memorizzato: nei test lo si forza in SQL
            self.env.flush_all()
            self.env.cr.execute("UPDATE gtms_trip SET state = %s WHERE id = %s", (stato, viaggio.id))
            viaggio.invalidate_recordset(['state'])
        return viaggio

    def _timesheet(self, inizio, fine, dipendente=None, viaggio=None, **valori):
        viaggio = viaggio or self._viaggio('TEST-TS', inizio, fine, autista=False)
        return self.env['account.analytic.line'].create(dict({
            'name': viaggio.name,
            'project_id': self.tipo.task_id.project_id.id,
            'task_id': self.tipo.task_id.id,
            'employee_id': (dipendente or self.dipendente).id,
            'date': self.caricamento._giorno_roma(inizio),
            'datetime_start': inizio,
            'datetime_stop': fine,
            'gtms_id': viaggio.id,
        }, **valori))

    def _righe_pwork(self, timesheet):
        return self.env['account.analytic.line.pwork'].search([('analytic_ids', 'in', timesheet.ids)])

    # ------------------------------------------------------------------
    # caricamento dei giorni
    # ------------------------------------------------------------------
    def test_10_giorno_con_viaggi_checked_viene_caricato(self):
        timesheet = self._timesheet(_roma(2030, 8, 3, 8), _roma(2030, 8, 3, 12))

        self.caricamento._carica_giorni()

        self.assertTrue(timesheet.validated)
        self.assertTrue(timesheet.processed)
        riga = self._righe_pwork(timesheet)
        self.assertEqual(len(riga), 1)
        self.assertTrue(riga.invio_automatico, "la riga creata dal caricamento va inviata dal cron")

    def test_11_viaggio_aperto_ferma_il_giorno_e_quelli_dopo(self):
        aperto = self._viaggio('TEST-APERTO', _roma(2030, 8, 3, 8), _roma(2030, 8, 3, 12), stato='planned')
        dopo = self._timesheet(_roma(2030, 8, 5, 8), _roma(2030, 8, 5, 12))

        esiti = self.caricamento._carica_giorni()

        self.assertEqual(esiti[-1]['giorno'], date(2030, 8, 3))
        self.assertEqual(esiti[-1]['viaggi_aperti'], aperto)
        self.assertFalse(dopo.validated, "i giorni si caricano in ordine")

    def test_12_viaggio_aperto_del_giorno_prima_ferma_il_giorno(self):
        """Il 3 ha tutto checked ma il 2 ha un viaggio aperto: il 3 non parte."""
        self._viaggio('TEST-APERTO-PRIMA', _roma(2030, 8, 2, 22), _roma(2030, 8, 3, 4), stato='planned')
        timesheet = self._timesheet(_roma(2030, 8, 3, 8), _roma(2030, 8, 3, 12))
        self.caricamento._carica_giorni()
        self.assertFalse(timesheet.validated)

    def test_13_viaggio_aperto_in_mese_chiuso_non_ferma(self):
        self._viaggio('TEST-APERTO-LUGLIO', _roma(2030, 7, 31, 8), _roma(2030, 7, 31, 12), stato='planned')
        timesheet = self._timesheet(_roma(2030, 8, 1, 8), _roma(2030, 8, 1, 12))
        self.caricamento._carica_giorni()
        self.assertTrue(timesheet.validated)

    def test_14_tipo_senza_spunta_non_ferma_e_non_si_carica(self):
        self._viaggio('TEST-SENZA-SPUNTA', _roma(2030, 8, 3, 8), _roma(2030, 8, 3, 12),
                      stato='planned', tipo=self.tipo_senza_spunta)
        viaggio_senza_spunta = self._viaggio('TEST-SENZA-SPUNTA-2', _roma(2030, 8, 3, 13), _roma(2030, 8, 3, 15),
                                             tipo=self.tipo_senza_spunta, autista=False)
        da_caricare = self._timesheet(_roma(2030, 8, 3, 8), _roma(2030, 8, 3, 12))
        escluso = self._timesheet(_roma(2030, 8, 3, 13), _roma(2030, 8, 3, 15), viaggio=viaggio_senza_spunta)

        self.caricamento._carica_giorni()

        self.assertTrue(da_caricare.validated)
        self.assertFalse(escluso.validated)

    def test_15_oggi_non_si_carica(self):
        timesheet = self._timesheet(_roma(2030, 9, 17, 6), _roma(2030, 9, 17, 9))
        ieri = self._timesheet(_roma(2030, 9, 16, 6), _roma(2030, 9, 16, 9))
        self.caricamento._carica_giorni()
        self.assertFalse(timesheet.validated)
        self.assertTrue(ieri.validated)

    def test_16_il_giorno_segue_il_metodo_di_pagamento(self):
        """Pianificato il 2, eseguito il 4 con ore effettive: e' un viaggio del 4."""
        viaggio = self._viaggio('TEST-SPOSTATO', _roma(2030, 8, 2, 8), _roma(2030, 8, 2, 12), stato='running',
                                pagamento='ore_effettive',
                                trip_start_from_survey=_roma(2030, 8, 4, 8), trip_end_from_survey=_roma(2030, 8, 4, 12))
        esiti = self.caricamento._carica_giorni()
        self.assertEqual(esiti[-1]['giorno'], date(2030, 8, 4))
        self.assertEqual(esiti[-1]['viaggi_aperti'], viaggio)

    def test_17_senza_orario_del_sondaggio_vale_il_pianificato(self):
        viaggio = self._viaggio('TEST-SENZA-SONDAGGIO', _roma(2030, 8, 2, 8), _roma(2030, 8, 2, 12),
                                stato='running', pagamento='ore_effettive')
        esiti = self.caricamento._carica_giorni()
        self.assertEqual(esiti[-1]['giorno'], date(2030, 8, 2))
        self.assertEqual(esiti[-1]['viaggi_aperti'], viaggio)

    # ------------------------------------------------------------------
    # turni sovrapposti
    # ------------------------------------------------------------------
    def test_20_turno_che_inizia_nel_precedente_viene_spostato(self):
        validato = self._timesheet(_roma(2030, 8, 3, 8), _roma(2030, 8, 3, 12))
        validato.write({'validated': True, 'processed': True})
        bozza = self._timesheet(_roma(2030, 8, 3, 11), _roma(2030, 8, 3, 15))

        self.caricamento._carica_giorni()

        self.assertEqual(bozza.datetime_start, _roma(2030, 8, 3, 12))
        self.assertEqual(validato.datetime_start, _roma(2030, 8, 3, 8), "i turni validati non si toccano")
        self.assertTrue(bozza.validated)

    def test_21_bozza_che_finisce_dentro_un_validato_ferma_il_giorno(self):
        validato = self._timesheet(_roma(2030, 8, 3, 10), _roma(2030, 8, 3, 14))
        validato.write({'validated': True, 'processed': True})
        bozza = self._timesheet(_roma(2030, 8, 3, 8), _roma(2030, 8, 3, 11))

        esiti = self.caricamento._carica_giorni()

        self.assertEqual(esiti[-1]['giorno'], date(2030, 8, 3))
        self.assertTrue(esiti[-1]['errori'])
        self.assertFalse(bozza.validated)
        self.assertEqual(validato.datetime_start, _roma(2030, 8, 3, 10))
        self.assertEqual(bozza.datetime_stop, _roma(2030, 8, 3, 11))

    def test_22_turno_contenuto_ferma_e_non_modifica_nulla(self):
        lungo = self._timesheet(_roma(2030, 8, 3, 8), _roma(2030, 8, 3, 16))
        corto = self._timesheet(_roma(2030, 8, 3, 10), _roma(2030, 8, 3, 12))
        spostabile = self._timesheet(_roma(2030, 8, 3, 15), _roma(2030, 8, 3, 18), dipendente=self.dipendente)

        esiti = self.caricamento._carica_giorni()

        self.assertTrue(esiti[-1]['errori'])
        self.assertFalse(lungo.validated or corto.validated)
        self.assertEqual(spostabile.datetime_start, _roma(2030, 8, 3, 15),
                         "con errori nel giorno le correzioni gia' fatte tornano indietro")

    def test_23_si_confronta_anche_il_giorno_prima(self):
        notte = self._timesheet(_roma(2030, 8, 2, 22), _roma(2030, 8, 3, 2))
        notte.write({'validated': True, 'processed': True})
        mattina = self._timesheet(_roma(2030, 8, 3, 1), _roma(2030, 8, 3, 6))

        self.caricamento._carica_giorni()

        self.assertEqual(mattina.datetime_start, _roma(2030, 8, 3, 2))

    def test_24_azione_manuale_lavora_solo_sulla_selezione(self):
        primo = self._timesheet(_roma(2030, 8, 3, 8), _roma(2030, 8, 3, 12))
        secondo = self._timesheet(_roma(2030, 8, 3, 11), _roma(2030, 8, 3, 15))
        altro_giorno = self._timesheet(_roma(2030, 8, 10, 8), _roma(2030, 8, 10, 12))
        altro_giorno_accavallato = self._timesheet(_roma(2030, 8, 10, 11), _roma(2030, 8, 10, 15))

        (primo | secondo).overlapping_time_management()

        self.assertEqual(secondo.datetime_start, _roma(2030, 8, 3, 12))
        self.assertEqual(altro_giorno_accavallato.datetime_start, _roma(2030, 8, 10, 11),
                         "i turni non selezionati non si toccano")

        contenuto = self._timesheet(_roma(2030, 8, 3, 9), _roma(2030, 8, 3, 10))
        with self.assertRaises(ValidationError):
            (primo | contenuto).overlapping_time_management()

    # ------------------------------------------------------------------
    # checked: sovrapposizione con ore gia' validate
    # ------------------------------------------------------------------
    def _ore_validate(self, inizio, fine):
        validato = self._timesheet(inizio, fine)
        validato.write({'validated': True, 'processed': True})
        return validato

    def test_30_checked_bloccato_se_finisce_dentro_ore_validate(self):
        self._ore_validate(_roma(2030, 8, 3, 10), _roma(2030, 8, 3, 14))
        viaggio = self._viaggio('TEST-CHECKED-PRIMA', _roma(2030, 8, 3, 8), _roma(2030, 8, 3, 11), stato=False)
        with self.assertRaises(ValidationError) as errore:
            viaggio.checked()
        self.assertIn('già validate', str(errore.exception))

    def test_31_checked_accettato_se_inizia_dentro_e_finisce_dopo(self):
        self._ore_validate(_roma(2030, 8, 3, 10), _roma(2030, 8, 3, 14))
        viaggio = self._viaggio('TEST-CHECKED-DOPO', _roma(2030, 8, 3, 12), _roma(2030, 8, 3, 16), stato=False)
        viaggio.checked()
        self.assertTrue(viaggio.check)

    def test_32_checked_bloccato_se_contenuto_in_ore_validate(self):
        self._ore_validate(_roma(2030, 8, 3, 8), _roma(2030, 8, 3, 16))
        viaggio = self._viaggio('TEST-CHECKED-DENTRO', _roma(2030, 8, 3, 10), _roma(2030, 8, 3, 12), stato=False)
        with self.assertRaises(ValidationError):
            viaggio.checked()

    # ------------------------------------------------------------------
    # mese chiuso e chiusura
    # ------------------------------------------------------------------
    def test_40_mese_chiuso_si_valida_ma_non_si_elabora(self):
        luglio = self._timesheet(_roma(2030, 7, 20, 8), _roma(2030, 7, 20, 12))
        luglio.write({'validated': True})
        self.assertTrue(luglio.validated, "la convalida resta possibile: il viaggio non si riapre")
        with self.assertRaises(UserError):
            luglio.processing_to_pwork()
        self.assertFalse(luglio.processed)
        with self.assertRaises(UserError):
            luglio.upload_to_pwork_table_2()
        self.assertFalse(self._righe_pwork(luglio))

    def test_41_non_si_chiude_un_mese_in_corso(self):
        impostazioni = self.env['res.config.settings'].create({
            'pwork_mese_chiuso': '9', 'pwork_anno_chiuso': 2030})
        with self.assertRaises(UserError):
            impostazioni.set_values()
        impostazioni = self.env['res.config.settings'].create({
            'pwork_mese_chiuso': '8', 'pwork_anno_chiuso': 2030})
        impostazioni.set_values()
        self.assertEqual(self.caricamento._ultimo_mese_chiuso(), date(2030, 8, 1))

    def test_42_chiusura_automatica_a_mese_finito(self):
        self.parametri.set_param('export_hours_to_pwork.caricamento_automatico', True)
        self.parametri.set_param('export_hours_to_pwork.chiusura_automatica', True)
        agosto = self._timesheet(_roma(2030, 8, 3, 8), _roma(2030, 8, 3, 12))

        self.caricamento._cron_caricamento()

        self.assertTrue(agosto.validated)
        self.assertEqual(self.caricamento._ultimo_mese_chiuso(), date(2030, 8, 1),
                         "agosto e' finito e completo, settembre e' in corso")

    def test_43_chiusura_non_avviene_con_viaggi_aperti(self):
        self.parametri.set_param('export_hours_to_pwork.caricamento_automatico', True)
        self.parametri.set_param('export_hours_to_pwork.chiusura_automatica', True)
        self._viaggio('TEST-APERTO-AGOSTO', _roma(2030, 8, 20, 8), _roma(2030, 8, 20, 12), stato='planned')

        self.caricamento._cron_caricamento()

        self.assertEqual(self.caricamento._ultimo_mese_chiuso(), date(2030, 7, 1))

    def test_44_alla_scadenza_si_carica_il_checked_e_si_chiude(self):
        self.parametri.set_param('export_hours_to_pwork.chiusura_automatica', True)
        self.parametri.set_param('export_hours_to_pwork.giorno_scadenza_chiusura', 3)
        aperto = self._viaggio('TEST-APERTO-SCADENZA', _roma(2030, 8, 10, 8), _roma(2030, 8, 10, 12), stato='planned')
        dopo_aperto = self._timesheet(_roma(2030, 8, 12, 8), _roma(2030, 8, 12, 12))

        esiti = self.caricamento._controlla_chiusura()

        self.assertTrue(dopo_aperto.validated, "alla scadenza i viaggi aperti non fermano i giorni dopo")
        self.assertEqual(self.caricamento._ultimo_mese_chiuso(), date(2030, 8, 1))
        self.assertTrue(esiti[0]['scadenza'])
        self.assertIn(aperto, esiti[0]['viaggi_aperti'])

    def test_46_rapporto_chiaro_alla_scadenza(self):
        """Chiuso alla scadenza nello stesso giro: niente "Caricamento fermo", si dice quanto e' stato caricato."""
        self.parametri.set_param('export_hours_to_pwork.chiusura_automatica', True)
        self.parametri.set_param('export_hours_to_pwork.giorno_scadenza_chiusura', 3)
        self._viaggio('TEST-APERTO-RAPPORTO', _roma(2030, 8, 10, 8), _roma(2030, 8, 10, 12), stato='planned')
        self._timesheet(_roma(2030, 8, 12, 8), _roma(2030, 8, 12, 12))

        caricamento = self.caricamento._carica_giorni()
        chiusure = self.caricamento._controlla_chiusura()
        testo = self.caricamento._testo_rapporto(caricamento, chiusure, None)

        self.assertTrue(caricamento[-1]['viaggi_aperti'], "il caricamento normale si era fermato al 10/08")
        self.assertNotIn("Caricamento fermo", testo)
        self.assertIn("Mese 08/2030 chiuso alla scadenza: caricati 1 timesheet (1 righe Pwork); restano fuori 1 viaggi non checked", testo)

    def test_45_senza_chiusura_automatica_nulla_si_chiude(self):
        self._timesheet(_roma(2030, 8, 3, 8), _roma(2030, 8, 3, 12))
        self.caricamento._carica_giorni()
        self.caricamento._controlla_chiusura()
        self.assertEqual(self.caricamento._ultimo_mese_chiuso(), date(2030, 7, 1))

    # ------------------------------------------------------------------
    # invio
    # ------------------------------------------------------------------
    def _riga_con_badge(self, automatica=True, dipendente=None):
        dipendente = dipendente or self.dipendente
        contratto = self.env['hr.contract'].search([('employee_id', '=', dipendente.id)], limit=1)
        if not self.env['hr.badgespwork'].search([('contract_ids', '=', contratto.id)]):
            self.env['hr.badgespwork'].create({
                'name': 'TEST-BADGE-%s' % dipendente.id, 'active': True,
                'valid_from': datetime(2025, 1, 1), 'hr_id': dipendente.id,
                'contract_ids': [(6, 0, contratto.ids)],
            })
        return self.env['account.analytic.line.pwork'].with_context(pwork_invio_automatico=automatica).create({
            'employee_id': dipendente.id,
            'datetime_start': _roma(2030, 8, 3, 8),
            'datetime_stop': _roma(2030, 8, 3, 12),
        })

    def _prepara_invio(self):
        self.parametri.set_param('switch_hr1', 'True')
        self.parametri.set_param('database.is_neutralized', False)

    def test_50_ogni_invio_e_risposta_vengono_registrati(self):
        self._prepara_invio()
        riga = self._riga_con_badge()
        risposta = (True, {'ckResponse': {'Esito': 2}}, False, 'payload di test')
        Righe = type(riga)
        with patch.object(Righe, 'send_timesheet', return_value=risposta), \
                patch.object(type(self.env['res.config.settings']), 'get_token_from_pwork', return_value=None):
            esito = self.caricamento._invia_righe_automatiche()

        self.assertEqual(esito['inviate'], 1)
        self.assertTrue(riga.pwork)
        self.assertEqual(len(riga.invio_ids), 1)
        self.assertTrue(riga.invio_ids.esito)
        self.assertEqual(riga.invio_ids.payload, 'payload di test')
        self.assertIn('Esito', riga.invio_ids.risposta)

    def test_51_database_neutralizzato_non_invia(self):
        self.parametri.set_param('database.is_neutralized', True)
        riga = self._riga_con_badge()
        with patch.object(type(riga), 'send_timesheet') as invio:
            esito = self.caricamento._invia_righe_automatiche()
            with self.assertRaises(UserError):
                riga.upload_to_pwork()
        invio.assert_not_called()
        self.assertTrue(esito['errori'])

    def test_52_errore_di_connessione_ferma_e_resta_registrato(self):
        self._prepara_invio()
        prima = self._riga_con_badge()
        seconda = self._riga_con_badge(dipendente=self.secondo_dipendente)
        with patch.object(type(prima), 'send_timesheet', side_effect=requests.exceptions.ConnectionError("rete giu'")) as invio, \
                patch.object(type(self.env['res.config.settings']), 'get_token_from_pwork', return_value=None):
            self.caricamento._invia_righe_automatiche()

        self.assertEqual(invio.call_count, 1, "dopo un errore di connessione non si prova con le altre righe")
        self.assertFalse(prima.pwork)
        self.assertIn('esito sconosciuto', prima.error_txt)
        self.assertEqual(len(prima.invio_ids), 1)
        self.assertFalse(seconda.error_txt)

    def test_53_il_cron_invia_solo_le_righe_automatiche(self):
        self._prepara_invio()
        manuale = self._riga_con_badge(automatica=False)
        with patch.object(type(manuale), 'send_timesheet') as invio, \
                patch.object(type(self.env['res.config.settings']), 'get_token_from_pwork', return_value=None):
            self.caricamento._invia_righe_automatiche()
        invio.assert_not_called()

    # ------------------------------------------------------------------
    # controllo HR e mail
    # ------------------------------------------------------------------
    def test_60_controllo_hr_solo_autisti_con_viaggi(self):
        senza_contratto = self.env['res.partner'].create({
            'name': 'Tre Senzacontratto', 'first_name': 'Tre', 'last_name': 'Senzacontratto'})
        self._viaggio('TEST-HR-1', _roma(2030, 8, 3, 8), _roma(2030, 8, 3, 12))
        self._viaggio('TEST-HR-2', _roma(2030, 8, 4, 8), _roma(2030, 8, 4, 12))
        self._viaggio('TEST-HR-3', _roma(2030, 8, 6, 8), _roma(2030, 8, 6, 12))
        self._viaggio('TEST-HR-4', _roma(2030, 8, 5, 8), _roma(2030, 8, 5, 12), autista=senza_contratto)
        # badge valido solo dal 04/08: il 03 resta scoperto
        self.env['hr.badgespwork'].create({
            'name': 'TEST-BADGE-HR', 'active': True, 'valid_from': datetime(2030, 8, 4),
            'hr_id': self.dipendente.id, 'contract_ids': [(6, 0, self.contratto.ids)],
        })

        anomalie = "\n".join(self.caricamento._controllo_hr(date(2030, 8, 3), date(2030, 8, 9)))

        self.assertIn(f"{self.autista.name}: senza badge valido il 03/08/2030", anomalie)
        self.assertNotIn("04/08/2030", anomalie, "giornata coperta dal badge")
        self.assertNotIn(senza_contratto.name, anomalie, "la mail HR parla solo di badge: il contratto mancante lo blocca il checked")
        self.assertNotIn(self.secondo_autista.name, anomalie,
                         "chi ha contratto senza badge ma nessun viaggio nel periodo non va segnalato")

    def test_61_mail_solo_con_indirizzo_e_una_volta(self):
        self.parametri.set_param('database.is_neutralized', False)
        bloccato = [{'giorno': date(2030, 8, 3), 'viaggi_aperti': self.env['gtms.trip'], 'errori': ['prova'], 'validati': 0, 'righe': 0}]
        Mail = self.env['mail.mail']
        prima = Mail.search_count([])

        self.caricamento._invia_rapporto(bloccato, [], None)
        self.assertEqual(Mail.search_count([]), prima, "senza indirizzo nessuna mail")

        self.parametri.set_param('export_hours_to_pwork.ultimo_rapporto', False)
        self.parametri.set_param('export_hours_to_pwork.email_avvisi', 'test@example.com')
        self.caricamento._invia_rapporto(bloccato, [], None)
        self.caricamento._invia_rapporto(bloccato, [], None)
        self.assertEqual(Mail.search_count([]), prima + 1, "lo stesso blocco non si ripete")

        # un invio riuscito e' una novita': la mail parte anche se il blocco e' lo stesso
        self.caricamento._invia_rapporto(bloccato, [], {'da_inviare': 2, 'inviate': 2, 'errori': []})
        self.assertEqual(Mail.search_count([]), prima + 2)

    def _mail_hr(self, giorno, ora=8):
        self.parametri.set_param('export_hours_to_pwork.email_hr', 'test@example.com')
        self.parametri.set_param('export_hours_to_pwork.ultimo_controllo_hr', False)
        Mail = self.env['mail.mail']
        ultima = Mail.search([], order='id desc', limit=1).id
        classe = 'odoo.addons.export_hours_to_pwork.models.pwork_caricamento.PworkCaricamento.'
        with patch(classe + '_oggi', lambda self: giorno), patch(classe + '_ora', lambda self: ora):
            self.caricamento._cron_controllo_hr()
        return Mail.search([('id', '>', ultima), ('email_to', '=', 'test@example.com')])

    def test_63_mail_hr_ultimo_giorno_lavorativo_del_mese(self):
        # viaggio gia' pianificato dopo l'ultimo giorno lavorativo (sabato 31/08/2030): va controllato lo stesso;
        # il dipendente di prova ha contratto ma nessun badge
        self._viaggio('TEST-HR-FINE-MESE', _roma(2030, 8, 31, 8), _roma(2030, 8, 31, 12), stato='planned')

        self.assertFalse(self._mail_hr(date(2030, 8, 29)), "giovedi' 29/08 non e' l'ultimo giorno lavorativo")
        self.assertFalse(self._mail_hr(date(2030, 8, 30), ora=6), "prima delle 7 non parte")
        mail = self._mail_hr(date(2030, 8, 30))
        self.assertEqual(len(mail), 1, "venerdi' 30/08/2030 e' l'ultimo giorno lavorativo di agosto")
        self.assertIn("Controllo del mese dal 01/08/2030 al 31/08/2030", mail.body_html)
        self.assertIn(f"{self.autista.name}: senza badge valido il 31/08/2030", mail.body_html)

    def test_64_festivita_spostano_l_ultimo_giorno_lavorativo(self):
        calendario = self.env.company.resource_calendar_id
        self.assertEqual(self.caricamento._ultimo_giorno_lavorativo(date(2030, 8, 10)), date(2030, 8, 30))
        self.env['resource.calendar.leaves'].create({
            'name': 'TEST chiusura aziendale',
            'calendar_id': calendario.id,
            'date_from': _roma(2030, 8, 30, 0),
            'date_to': _roma(2030, 8, 30, 23, 59),
        })
        self.assertEqual(self.caricamento._ultimo_giorno_lavorativo(date(2030, 8, 10)), date(2030, 8, 29))

    # ------------------------------------------------------------------
    # metodo precedente
    # ------------------------------------------------------------------
    def test_70_metodo_precedente_ferma_la_nuova_gestione(self):
        self.parametri.set_param('export_hours_to_pwork.caricamento_automatico', True)
        self.parametri.set_param('export_hours_to_pwork.metodo_precedente', True)
        bozza = self._timesheet(_roma(2030, 8, 3, 8), _roma(2030, 8, 3, 12))

        self.caricamento._cron_caricamento()
        self.assertFalse(bozza.validated, "con il metodo precedente il cron non carica")
        with self.assertRaises(UserError):
            self.caricamento._esegui_manuale(date(2030, 9, 16))
        self.assertFalse(self._mail_hr(date(2030, 8, 30)), "niente controllo HR")

        # checked come prima: nessun blocco sulle ore gia' validate
        self._ore_validate(_roma(2030, 8, 5, 10), _roma(2030, 8, 5, 14))
        viaggio = self._viaggio('TEST-PRECEDENTE-CHECKED', _roma(2030, 8, 5, 8), _roma(2030, 8, 5, 11), stato=False)
        viaggio.checked()
        self.assertTrue(viaggio.check)

        # mese chiuso: l'elaborazione non e' bloccata
        luglio = self._timesheet(_roma(2030, 7, 20, 8), _roma(2030, 7, 20, 12))
        luglio.write({'validated': True})
        luglio.upload_to_pwork_table_2()
        self.assertTrue(self._righe_pwork(luglio))

    def test_33_niente_timesheet_doppi_sullo_stesso_viaggio(self):
        """Il checked ripetuto o incrociato con la rigenerazione non deve creare doppioni."""
        viaggio = self._viaggio('TEST-DOPPIONI', _roma(2030, 8, 3, 8), _roma(2030, 8, 3, 12), stato=False,
                                allievo=self.secondo_autista)
        viaggio.checked()
        timesheet = self.env['account.analytic.line'].search([('gtms_id', '=', viaggio.id)])
        self.assertEqual(len(timesheet), 2)

        viaggio.check = False
        viaggio.checked()
        viaggio.regenerate_hours_to_timesheet()

        self.assertEqual(len(self.env['account.analytic.line'].search([('gtms_id', '=', viaggio.id)])), 2,
                         "le ore restano una per dipendente")

    def test_34_messaggio_chiaro_sui_timesheet_doppi(self):
        primo = self._timesheet(_roma(2030, 8, 3, 8), _roma(2030, 8, 3, 12))
        doppione = self._timesheet(_roma(2030, 8, 3, 8), _roma(2030, 8, 3, 12), viaggio=primo.gtms_id)

        errori = (primo | doppione)._gestisci_sovrapposizioni()

        self.assertEqual(len(errori), 1)
        self.assertIn("due timesheet doppi", errori[0][1])
        self.assertIn(f"id {primo.id} e {doppione.id}", errori[0][1])

    # ------------------------------------------------------------------
    # esterni (is_esterno sul contatto)
    # ------------------------------------------------------------------
    def _esterno(self, nome):
        return self.env['res.partner'].create({'name': nome, 'first_name': nome, 'last_name': 'Esterno', 'is_esterno': True})

    def test_80_autista_esterno_checked_senza_timesheet(self):
        viaggio = self._viaggio('TEST-ESTERNO', _roma(2030, 8, 3, 8), _roma(2030, 8, 3, 12), stato=False,
                                autista=self._esterno('Cinque'))
        viaggio.checked()
        self.assertTrue(viaggio.check)
        self.assertFalse(self.env['account.analytic.line'].search([('gtms_id', '=', viaggio.id)]))

    def test_81_allievo_esterno_saltato_senza_errore(self):
        viaggio = self._viaggio('TEST-ALLIEVO-ESTERNO', _roma(2030, 8, 3, 8), _roma(2030, 8, 3, 12), stato=False,
                                allievo=self._esterno('Sei'))
        viaggio.checked()
        timesheet = self.env['account.analytic.line'].search([('gtms_id', '=', viaggio.id)])
        self.assertEqual(timesheet.employee_id, self.dipendente, "solo l'autista interno ha le ore")
        viaggio.regenerate_hours_to_timesheet()  # l'allievo esterno non e' un timesheet mancante
        self.assertEqual(len(self.env['account.analytic.line'].search([('gtms_id', '=', viaggio.id)])), 1)

    def test_82_viaggio_di_soli_esterni_aperto_non_ferma_il_caricamento(self):
        self._viaggio('TEST-ESTERNO-APERTO', _roma(2030, 8, 3, 8), _roma(2030, 8, 3, 12), stato='planned',
                      autista=self._esterno('Sette'))
        timesheet = self._timesheet(_roma(2030, 8, 3, 13), _roma(2030, 8, 3, 16))
        self.caricamento._carica_giorni()
        self.assertTrue(timesheet.validated)

    def test_83_viaggio_con_interno_e_allievo_esterno_aperto_ferma(self):
        aperto = self._viaggio('TEST-MISTO-APERTO', _roma(2030, 8, 3, 8), _roma(2030, 8, 3, 12), stato='planned',
                               allievo=self._esterno('Otto'))
        esiti = self.caricamento._carica_giorni()
        self.assertEqual(esiti[-1]['viaggi_aperti'], aperto, "l'autista interno ha ore da caricare")

    def test_84_esterni_aperti_non_bloccano_la_chiusura(self):
        self.parametri.set_param('export_hours_to_pwork.chiusura_automatica', True)
        esterno = self._esterno('Nove')
        self._viaggio('TEST-ESTERNO-DA-CHIUDERE', _roma(2030, 8, 20, 8), _roma(2030, 8, 20, 12), stato='planned', autista=esterno)

        self.caricamento._controlla_chiusura()

        self.assertEqual(self.caricamento._ultimo_mese_chiuso(), date(2030, 8, 1))
        anomalie = "\n".join(self.caricamento._controllo_hr(date(2030, 8, 1), date(2030, 8, 31)))
        self.assertNotIn(esterno.name, anomalie, "gli esterni non hanno badge per definizione")

    # ------------------------------------------------------------------
    # promemoria ai ROP
    # ------------------------------------------------------------------
    def _promemoria(self, giorno=OGGI, ora=9, minuti=0):
        classe = 'odoo.addons.export_hours_to_pwork.models.pwork_caricamento.PworkCaricamento.'
        Mail = self.env['mail.mail']
        ultima = Mail.search([], order='id desc', limit=1).id
        with patch(classe + '_oggi', lambda self: giorno), patch(classe + '_ora', lambda self: ora), \
                patch(classe + '_minuti', lambda self: minuti):
            self.caricamento._cron_promemoria_rop()
        return Mail.search([('id', '>', ultima), ('subject', 'like', 'Viaggi ancora da chiudere')])

    def test_90_promemoria_rop_ai_follower_del_centro_di_costo(self):
        organizzazione = self.env['res.partner'].create({'name': 'TEST - Cdc', 'type': 'delivery', 'is_company': True})
        self.tipo.organization_id = organizzazione
        team = self.env['helpdesk.team'].create({'name': 'TEST ROP', 'organization_id': organizzazione.id})
        rop = self.env['res.partner'].create({'name': 'TEST Rop', 'email': 'rop@example.com'})
        team.message_subscribe(partner_ids=rop.ids)
        self.parametri.set_param('export_hours_to_pwork.email_hr', 'hr@example.com')
        allievo = self._crea_autista('Dieci', 'Allievo')[0]
        self._viaggio('TEST-DA-CHIUDERE', _roma(2030, 9, 10, 8), _roma(2030, 9, 10, 12), stato='planned', allievo=allievo)
        self._viaggio('TEST-TROPPO-VECCHIO', _roma(2030, 8, 1, 8), _roma(2030, 8, 1, 12), stato='planned')

        self.assertFalse(self._promemoria(), "interruttore spento: nessun promemoria")
        self.parametri.set_param('export_hours_to_pwork.promemoria_rop', True)
        self.assertFalse(self._promemoria(ora=8, minuti=29), "prima delle 08:30 non parte")
        mail = self._promemoria(ora=8, minuti=30)

        self.assertEqual(len(mail), 1)
        self.assertEqual(mail.subject, 'Viaggi ancora da chiudere - TEST - Cdc')
        self.assertIn('rop@example.com', mail.email_to)
        self.assertEqual(mail.email_cc, 'hr@example.com')
        self.assertIn('10/09/2030', mail.body_html)
        self.assertIn(f"TEST-DA-CHIUDERE | Autista: {self.autista.name} - Affiancato: {allievo.name}", mail.body_html)
        self.assertNotIn('TEST-TROPPO-VECCHIO', mail.body_html, "oltre i 30 giorni non si ricorda")
        self.assertFalse(self._promemoria(ora=10), "una volta sola al giorno")

    def test_62_database_neutralizzato_mail_annullata(self):
        """Sulle copie la mail si legge in Odoo ma non parte: nasce gia' annullata."""
        self.parametri.set_param('database.is_neutralized', True)
        self.parametri.set_param('export_hours_to_pwork.email_hr', 'test@example.com')
        mail = self.caricamento._invia_mail('email_hr', 'prova', 'prova')
        self.assertEqual(mail.state, 'cancel')
        self.assertEqual(mail.email_to, 'test@example.com')
