"""Test delle correzioni al caricamento ore verso Pwork.

Coprono:
- il checked che crea le ore di autista e allievo anche quando lo fa un ROP, e che si ferma con un
  errore chiaro se uno dei due non ha un contratto valido;
- l'unchecked bloccato quando il viaggio ha timesheet validati, elaborati o gia' su Pwork;
- il "Reimposta a bozza" bloccato sui timesheet gia' elaborati o su Pwork;
- "Badge mancante" scritto solo sulla riga Pwork senza badge.

Nessun test esce verso Pwork: ogni chiamata HTTP fatta con `requests` fa fallire il test.
I test girano in transazione e il database viene riportato indietro alla fine.

Si lanciano con:
    odoo17-bin -d <db> --test-enable --test-tags /export_hours_to_pwork \
        --stop-after-init --http-port=8179 --log-level=warn
"""

from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


def _http_vietato(*args, **kwargs):
    raise AssertionError(
        "Il test ha tentato una chiamata HTTP (Pwork o altro): nei test non si invia nulla."
    )


@tagged('post_install', '-at_install')
class TestCorrezioniCaricamentoOre(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # --- blocco di sicurezza: nessuna chiamata HTTP ---------------------
        # requests.post/get passano tutti da Session.request, quindi si blocca anche
        # quello che parte da altri moduli (pwork_connector, hr1...) durante le create
        blocco = patch('requests.sessions.Session.request', _http_vietato)
        blocco.start()
        cls.addClassCleanup(blocco.stop)

        # --- l'automazione "Controllo timesheet generati secondo autista" va eliminata -----
        # le ore dell'allievo deve crearle checked(): nei test la si spegne, cosi' si prova il
        # comportamento senza la toppa (la disattivazione torna indietro con la transazione)
        cls.env['base.automation'].search(
            [('name', '=', 'Controllo timesheet generati secondo autista')]).write({'active': False})

        cls.adesso = fields.Datetime.now()

        cls.tipo_viaggio = cls.env['gtms.trip.type'].search(
            [('task_id', '!=', False), ('task_id.project_id', '!=', False)], limit=1)
        cls.veicolo = cls.env['fleet.vehicle'].search([], limit=1)

        cls.autista, cls.dipendente, cls.contratto = cls._crea_autista('Uno', 'Autista')
        cls.allievo, cls.dipendente_allievo, _contratto = cls._crea_autista('Due', 'Allievo')
        cls.senza_contratto = cls.env['res.partner'].create({
            'name': 'Tre Scaduto', 'first_name': 'Tre', 'last_name': 'Scaduto'})
        dipendente_scaduto = cls._crea_dipendente('Tre', 'Scaduto', cls.senza_contratto)
        cls.env['hr.contract'].create({
            'name': 'Contratto scaduto test',
            'employee_id': dipendente_scaduto.id,
            'date_start': (cls.adesso - timedelta(days=730)).date(),
            'date_end': (cls.adesso - timedelta(days=365)).date(),
            'wage': 1,
        })

    @classmethod
    def _crea_dipendente(cls, nome, cognome, partner):
        # nome e cognome separati: la create di hr1_update li usa per generare l'utente
        return cls.env['hr.employee'].create({
            'name': '%s %s' % (nome, cognome),
            'first_name': nome,
            'last_name': cognome,
            'address_home_id': partner.id,
        })

    @classmethod
    def _crea_autista(cls, nome, cognome):
        partner = cls.env['res.partner'].create({
            'name': '%s %s' % (nome, cognome), 'first_name': nome, 'last_name': cognome})
        dipendente = cls._crea_dipendente(nome, cognome, partner)
        contratto = cls.env['hr.contract'].create({
            'name': 'Contratto test %s' % cognome,
            'employee_id': dipendente.id,
            'date_start': (cls.adesso - timedelta(days=365)).date(),
            'wage': 1,
            'state': 'open',
        })
        return partner, dipendente, contratto

    def _crea_viaggio(self, nome, autista=None, allievo=None):
        viaggio = self.env['gtms.trip'].create({
            'name': nome,
            'trip_type_id': self.tipo_viaggio.id,
            'first_stop_planned_at': self.adesso - timedelta(hours=6),
            'last_stop_planned_at': self.adesso - timedelta(hours=2),
            'drivers_payment': 'ore_pianificate',
        })
        # la riga veicolo/autista va creata a parte: la create di trip_vehicle_manager.py
        # accetta un solo dizionario, non la lista che arriva da Command.create
        riga = {
            'trip_id': viaggio.id,
            'driver_id': (autista or self.autista).id,
            'fleet_vehicle_id': self.veicolo.id,
        }
        if allievo:
            riga['learning_driver_id'] = allievo.id
        self.env['gtms.trip.vehicle.manager'].create(riga)
        return viaggio

    def _timesheet_del_viaggio(self, viaggio):
        return self.env['account.analytic.line'].search([('gtms_id', '=', viaggio.id)])

    # ------------------------------------------------------------------
    # dati di partenza
    # ------------------------------------------------------------------
    def test_00_i_dati_di_prova_sono_coerenti(self):
        """Se questo fallisce, gli altri test non provano nulla di utile."""
        self.assertTrue(self.tipo_viaggio, "serve un tipo viaggio con task e progetto")
        self.assertTrue(self.veicolo, "serve almeno un veicolo")
        viaggio = self._crea_viaggio('TEST-COERENZA')
        self.assertEqual(viaggio.current_driver_id, self.autista)
        self.assertEqual(viaggio.current_fleet_id, self.veicolo)
        self.assertTrue(viaggio.company_ids, "il viaggio deve avere una societa'")

    # ------------------------------------------------------------------
    # checked(): contratto mancante
    # ------------------------------------------------------------------
    def test_10_checked_con_contratto_crea_il_timesheet(self):
        viaggio = self._crea_viaggio('TEST-CHECKED-OK')
        viaggio.checked()
        timesheet = self._timesheet_del_viaggio(viaggio)
        self.assertEqual(timesheet.employee_id, self.dipendente)
        self.assertTrue(viaggio.check)

    def test_11_autista_senza_contratto_da_errore(self):
        """Prima il viaggio restava non checked senza nessun messaggio."""
        viaggio = self._crea_viaggio('TEST-SENZA-CONTRATTO', autista=self.senza_contratto)
        with self.assertRaises(ValidationError) as errore:
            viaggio.checked()
        self.assertIn(self.senza_contratto.name, str(errore.exception))
        self.assertFalse(viaggio.check)
        self.assertFalse(self._timesheet_del_viaggio(viaggio))

    def test_12_allievo_senza_contratto_da_errore(self):
        """Prima il viaggio passava a checked con le sole ore dell'autista."""
        viaggio = self._crea_viaggio('TEST-ALLIEVO-SENZA', allievo=self.senza_contratto)
        with self.assertRaises(ValidationError) as errore:
            viaggio.checked()
        self.assertIn(self.senza_contratto.name, str(errore.exception))
        self.assertFalse(self._timesheet_del_viaggio(viaggio),
                         "nemmeno le ore dell'autista devono restare")

    def test_13_allievo_con_contratto_crea_due_timesheet(self):
        viaggio = self._crea_viaggio('TEST-ALLIEVO-OK', allievo=self.allievo)
        viaggio.checked()
        self.assertEqual(self._timesheet_del_viaggio(viaggio).employee_id,
                         self.dipendente | self.dipendente_allievo)

    def test_14_rop_crea_le_ore_anche_dell_allievo(self):
        """Un ROP non vede il contratto dell'allievo: prima checked() saltava le sue ore in silenzio
        (per questo esisteva l'automazione). Ora le crea checked(), una volta sola."""
        rop =self.env['res.users'].search([
            ('groups_id', 'in', self.env.ref('Diritti.rop_group').id),
            ('groups_id', 'not in', self.env.ref('hr_contract.group_hr_contract_manager').id),
            ('company_ids', 'in', self.dipendente_allievo.company_id.id),
        ], limit=1)
        self.assertTrue(rop, "serve un ROP senza diritti da responsabile contratti")
        self.assertFalse(
            self.env['hr.contract'].with_user(rop).search([('employee_id', '=', self.dipendente_allievo.id)]),
            "il ROP di prova non deve vedere il contratto dell'allievo, altrimenti il test non prova nulla")

        viaggio = self._crea_viaggio('TEST-ROP-ALLIEVO', allievo=self.allievo)
        viaggio.with_user(rop).checked()

        timesheet = self._timesheet_del_viaggio(viaggio)
        self.assertTrue(viaggio.check)
        self.assertEqual(len(timesheet.filtered(lambda t: t.employee_id == self.dipendente_allievo)), 1)
        self.assertEqual(len(timesheet.filtered(lambda t: t.employee_id == self.dipendente)), 1)

    # ------------------------------------------------------------------
    # unchecked()
    # ------------------------------------------------------------------
    def test_20_unchecked_su_bozza_cancella_i_timesheet(self):
        viaggio = self._crea_viaggio('TEST-UNCHECK-BOZZA')
        viaggio.checked()
        viaggio.unchecked()
        self.assertFalse(viaggio.check)
        self.assertFalse(self._timesheet_del_viaggio(viaggio))

    def test_21_unchecked_bloccato_con_timesheet_validato(self):
        viaggio = self._crea_viaggio('TEST-UNCHECK-VALIDATO')
        viaggio.checked()
        self._timesheet_del_viaggio(viaggio).write({'validated': True})
        with self.assertRaises(ValidationError):
            viaggio.unchecked()

    def test_22_unchecked_bloccato_con_timesheet_elaborato_non_validato(self):
        """Caso sfuggito prima: elaborato ma riportato in bozza, lo stato calcolato era 'draft'."""
        viaggio = self._crea_viaggio('TEST-UNCHECK-ELABORATO')
        viaggio.checked()
        timesheet = self._timesheet_del_viaggio(viaggio)
        timesheet.write({'processed': True})
        self.assertEqual(timesheet.validated_status, 'draft')
        with self.assertRaises(ValidationError):
            viaggio.unchecked()
        self.assertTrue(timesheet.exists())

    # ------------------------------------------------------------------
    # Reimposta a bozza
    # ------------------------------------------------------------------
    def test_30_bozza_bloccata_su_timesheet_elaborato(self):
        viaggio = self._crea_viaggio('TEST-BOZZA-ELABORATO')
        viaggio.checked()
        timesheet = self._timesheet_del_viaggio(viaggio)
        timesheet.write({'validated': True, 'processed': True})
        with self.assertRaises(UserError):
            timesheet.write({'validated': False})

    def test_31_bozza_consentita_su_timesheet_solo_validato(self):
        viaggio = self._crea_viaggio('TEST-BOZZA-VALIDATO')
        viaggio.checked()
        timesheet = self._timesheet_del_viaggio(viaggio)
        timesheet.write({'validated': True})
        timesheet.write({'validated': False})
        self.assertFalse(timesheet.validated)

    # ------------------------------------------------------------------
    # Upload to Pwork: "Badge mancante"
    # ------------------------------------------------------------------
    def test_40_badge_mancante_solo_sulla_riga_senza_badge(self):
        self.env['ir.config_parameter'].sudo().set_param('switch_hr1', 'True')
        # la copia locale e' neutralizzata: nel test l'invio e' comunque finto
        self.env['ir.config_parameter'].sudo().set_param('database.is_neutralized', False)
        self.env['hr.badgespwork'].create({
            'name': 'TEST-BADGE-CARICAMENTO-ORE',
            'active': True,
            'valid_from': self.adesso - timedelta(days=365),
            'hr_id': self.dipendente.id,
            'contract_ids': [Command.set(self.contratto.ids)],
        })
        righe = self.env['account.analytic.line.pwork']
        # la riga con badge viene elaborata per prima: e' quella che prima veniva sovrascritta
        for dipendente in (self.dipendente, self.dipendente_allievo):
            righe |= righe.create({
                'employee_id': dipendente.id,
                'datetime_start': self.adesso - timedelta(hours=6),
                'datetime_stop': self.adesso - timedelta(hours=2),
            })
        riga_con_badge, riga_senza_badge = righe

        risposta_ok = (True, {'ckResponse': {'Esito': 2}}, False, 'payload di test')
        with patch.object(type(righe), 'send_timesheet', return_value=risposta_ok) as invio:
            righe.upload_to_pwork()

        self.assertEqual(invio.call_count, 1, "va inviata solo la riga con il badge")
        self.assertTrue(riga_con_badge.pwork)
        self.assertNotEqual(riga_con_badge.error_txt, 'Badge mancante')
        self.assertEqual(riga_con_badge.validated_status, 'done')
        self.assertFalse(riga_senza_badge.pwork)
        self.assertEqual(riga_senza_badge.error_txt, 'Badge mancante')
        self.assertEqual(riga_senza_badge.validated_status, 'error')
