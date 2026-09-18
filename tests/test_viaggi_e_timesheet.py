"""Test del flusso viaggio -> timesheet di export_hours_to_pwork.

Coprono quello che si e' rotto passando alla 19 e quello che si tocca piu' spesso:
il controllo delle sovrapposizioni, la creazione del timesheet in `checked()` e la
sistemazione degli orari di `overlapping_time_management`.

Nessun test esce verso Pwork: `requests.post` viene sostituito per tutta la durata
della classe con una funzione che fa fallire il test se qualcuno prova a chiamarla.
I test girano in transazione e il database viene riportato indietro alla fine.

Si lanciano con:
    odoo19-bin -c odoo19.conf -d <db> --test-enable \
        --test-tags /export_hours_to_pwork --stop-after-init
"""

from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


def _pwork_vietato(*args, **kwargs):
    raise AssertionError(
        "Il test ha tentato una chiamata HTTP verso Pwork: nei test non si invia nulla."
    )


@tagged('post_install', '-at_install')
class TestViaggiETimesheet(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # --- blocco di sicurezza verso Pwork -------------------------------
        # patchare requests.post dentro il modulo copre anche res_config_settings,
        # perche' l'oggetto `requests` e' lo stesso in tutto il processo.
        blocco = patch(
            'odoo.addons.export_hours_to_pwork.models.account_analytic_line_pwork.requests.post',
            _pwork_vietato,
        )
        blocco.start()
        cls.addClassCleanup(blocco.stop)

        cls.adesso = fields.Datetime.now()

        # --- tipo di viaggio, con il task su cui finiranno le ore ----------
        cls.tipo_viaggio = cls.env['gtms.trip.type'].search(
            [('task_id', '!=', False), ('task_id.project_id', '!=', False)], limit=1)
        if not cls.tipo_viaggio:
            progetto = cls.env['project.project'].create({'name': 'Progetto test viaggi'})
            task = cls.env['project.task'].create(
                {'name': 'Task test viaggi', 'project_id': progetto.id})
            sequenza = cls.env['ir.sequence'].create(
                {'name': 'Sequenza test viaggi', 'code': 'test.viaggi.seq', 'padding': 4})
            cls.tipo_viaggio = cls.env['gtms.trip.type'].create({
                'name': 'Tipo viaggio test',
                'task_id': task.id,
                'ir_sequence_id': sequenza.id,
            })

        # --- veicolo -------------------------------------------------------
        cls.veicolo = cls.env['fleet.vehicle'].search([], limit=1)
        if not cls.veicolo:
            modello = cls.env['fleet.vehicle.model'].search([], limit=1)
            cls.veicolo = cls.env['fleet.vehicle'].create({'model_id': modello.id})

        # --- autista: partner + dipendente + contratto (hr.version) --------
        cls.autista = cls.env['res.partner'].create({
            'name': 'Uno Autista', 'first_name': 'Uno', 'last_name': 'Autista'})
        cls.dipendente = cls._crea_dipendente('Uno', 'Dipendente', cls.autista)

        cls.secondo_autista = cls.env['res.partner'].create({
            'name': 'Due Autista', 'first_name': 'Due', 'last_name': 'Autista'})
        cls.secondo_dipendente = cls._crea_dipendente('Due', 'Dipendente', cls.secondo_autista)

    @classmethod
    def _crea_dipendente(cls, nome, cognome, partner):
        """Dipendente collegato al partner autista, con un contratto aperto.

        nome e cognome vanno passati separati: la create di hr1_update genera l'utente
        leggendo first_name/last_name e non regge un dipendente che ne e' privo.
        """
        dipendente = cls.env['hr.employee'].create({
            'name': '%s %s' % (nome, cognome),
            'first_name': nome,
            'last_name': cognome,
            'address_home_id': partner.id,
        })
        # in 19 il contratto e' la versione del dipendente
        dipendente.version_id.write({
            'contract_date_start': (cls.adesso - timedelta(days=365)).date(),
            'contract_date_end': False,
        })
        return dipendente

    def _crea_viaggio(self, nome, inizio, fine, autista=None, pagamento='ore_pianificate'):
        return self.env['gtms.trip'].create({
            'name': nome,
            'trip_type_id': self.tipo_viaggio.id,
            'first_stop_planned_at': inizio,
            'last_stop_planned_at': fine,
            'drivers_payment': pagamento,
            'trip_vehicle_manager_ids': [Command.create({
                'driver_id': (autista or self.autista).id,
                'fleet_vehicle_id': self.veicolo.id,
            })],
        })

    def _forza_stato(self, viaggio, stato):
        """`state` e' calcolato e memorizzato: nei test lo si forza in SQL."""
        self.env.cr.execute(
            "UPDATE gtms_trip SET state = %s WHERE id = %s", (stato, viaggio.id))
        viaggio.invalidate_recordset(['state'])

    # ------------------------------------------------------------------
    # dati di partenza
    # ------------------------------------------------------------------
    def test_00_i_dati_di_prova_sono_coerenti(self):
        """Se questo fallisce, gli altri test non provano nulla di utile."""
        viaggio = self._crea_viaggio(
            'TEST-COERENZA', self.adesso - timedelta(hours=5), self.adesso - timedelta(hours=1))
        self.assertEqual(viaggio.current_driver_id, self.autista,
                         "l'autista corrente si ricava dalla riga veicolo/autista")
        self.assertEqual(viaggio.current_fleet_id, self.veicolo)
        self.assertTrue(viaggio.company_ids, "il viaggio deve avere una societa'")
        self.assertTrue(self.tipo_viaggio.task_id.project_id,
                        "il tipo viaggio deve puntare a un task con progetto")
        self.assertEqual(self.dipendente.address_home_id, self.autista)
        self.assertTrue(self.dipendente.contract_date_start,
                        "il dipendente deve avere un contratto, altrimenti checked() si ferma")

    # ------------------------------------------------------------------
    # checked(): creazione del timesheet
    # ------------------------------------------------------------------
    def test_10_checked_crea_il_timesheet(self):
        inizio = self.adesso - timedelta(hours=6)
        fine = self.adesso - timedelta(hours=2)
        viaggio = self._crea_viaggio('TEST-CHECKED-1', inizio, fine)

        viaggio.checked()

        timesheet = self.env['account.analytic.line'].search([('gtms_id', '=', viaggio.id)])
        self.assertEqual(len(timesheet), 1, "checked() deve creare un timesheet e uno solo")
        self.assertEqual(timesheet.employee_id, self.dipendente)
        self.assertEqual(timesheet.datetime_start, inizio)
        self.assertEqual(timesheet.datetime_stop, fine)
        self.assertEqual(timesheet.task_id, self.tipo_viaggio.task_id)
        self.assertTrue(viaggio.check, "il viaggio va marcato come controllato")

    def test_11_viaggio_non_pagabile_non_crea_timesheet(self):
        viaggio = self._crea_viaggio(
            'TEST-NON-PAGABILE', self.adesso - timedelta(hours=4), self.adesso - timedelta(hours=1),
            pagamento='non_pagabile')

        viaggio.checked()

        self.assertFalse(self.env['account.analytic.line'].search([('gtms_id', '=', viaggio.id)]),
                         "un viaggio non pagabile non deve generare ore")
        self.assertTrue(viaggio.check)

    def test_12_senza_metodo_di_pagamento_si_ferma(self):
        viaggio = self._crea_viaggio(
            'TEST-SENZA-PAGAMENTO', self.adesso - timedelta(hours=4),
            self.adesso - timedelta(hours=1), pagamento=False)
        with self.assertRaises(ValidationError):
            viaggio.checked()

    # ------------------------------------------------------------------
    # _check_overlapping_trips()
    # ------------------------------------------------------------------
    def test_20_sovrapposizione_stesso_autista_bloccata(self):
        """Due viaggi dello stesso autista che si accavallano: il secondo non passa."""
        primo = self._crea_viaggio(
            'TEST-SOVRAP-A', self.adesso - timedelta(hours=8), self.adesso - timedelta(hours=4))
        self._forza_stato(primo, 'checked')

        # stessa finestra oraria, stesso autista
        secondo = self._crea_viaggio(
            'TEST-SOVRAP-B', self.adesso - timedelta(hours=7), self.adesso - timedelta(hours=5))

        with self.assertRaises(ValidationError) as errore:
            secondo._check_overlapping_trips()
        self.assertIn(self.autista.name, str(errore.exception),
                      "il messaggio deve dire quale autista e' in conflitto")

    def test_21_autisti_diversi_non_si_bloccano(self):
        primo = self._crea_viaggio(
            'TEST-DIVERSI-A', self.adesso - timedelta(hours=8), self.adesso - timedelta(hours=4))
        self._forza_stato(primo, 'checked')

        secondo = self._crea_viaggio(
            'TEST-DIVERSI-B', self.adesso - timedelta(hours=7), self.adesso - timedelta(hours=5),
            autista=self.secondo_autista)

        secondo._check_overlapping_trips()  # non deve sollevare nulla

    def test_22_sovrapposizione_consentita_a_cavallo(self):
        """Il caso ammesso dal codice: il nuovo turno inizia dentro il precedente e finisce dopo."""
        primo = self._crea_viaggio(
            'TEST-CAVALLO-A', self.adesso - timedelta(hours=8), self.adesso - timedelta(hours=4))
        self._forza_stato(primo, 'checked')

        secondo = self._crea_viaggio(
            'TEST-CAVALLO-B', self.adesso - timedelta(hours=6), self.adesso - timedelta(hours=1))

        secondo._check_overlapping_trips()  # consentito: non deve sollevare

    # ------------------------------------------------------------------
    # overlapping_time_management()
    # ------------------------------------------------------------------
    def _crea_timesheet(self, dipendente, inizio, fine, nome='TEST-TS'):
        return self.env['account.analytic.line'].create({
            'name': nome,
            'project_id': self.tipo_viaggio.task_id.project_id.id,
            'task_id': self.tipo_viaggio.task_id.id,
            'employee_id': dipendente.id,
            'date': inizio.date(),
            'datetime_start': inizio,
            'datetime_stop': fine,
        })

    def test_40_gli_orari_accavallati_vengono_allineati(self):
        primo_inizio = self.adesso - timedelta(days=2, hours=8)
        primo_fine = self.adesso - timedelta(days=2, hours=4)
        # il secondo comincia un'ora prima che finisca il primo
        secondo_inizio = self.adesso - timedelta(days=2, hours=5)
        secondo_fine = self.adesso - timedelta(days=2, hours=1)

        self._crea_timesheet(self.dipendente, primo_inizio, primo_fine, 'TEST-TS-1')
        secondo = self._crea_timesheet(self.dipendente, secondo_inizio, secondo_fine, 'TEST-TS-2')

        self.env['account.analytic.line'].overlapping_time_management()

        self.assertEqual(secondo.datetime_start, primo_fine,
                         "l'inizio del secondo turno va spostato alla fine del primo")

    def test_41_i_timesheet_oltre_i_30_giorni_non_si_toccano(self):
        """La funzione guarda solo l'ultimo mese: i turni vecchi restano come sono."""
        vecchio_inizio = self.adesso - timedelta(days=90, hours=8)
        vecchio_fine = self.adesso - timedelta(days=90, hours=4)
        accavallato_inizio = self.adesso - timedelta(days=90, hours=5)
        accavallato_fine = self.adesso - timedelta(days=90, hours=1)

        self._crea_timesheet(self.dipendente, vecchio_inizio, vecchio_fine, 'TEST-TS-VECCHIO-1')
        vecchio = self._crea_timesheet(
            self.dipendente, accavallato_inizio, accavallato_fine, 'TEST-TS-VECCHIO-2')

        self.env['account.analytic.line'].overlapping_time_management()

        self.assertEqual(vecchio.datetime_start, accavallato_inizio,
                         "un turno di tre mesi fa non deve essere modificato")

    # ------------------------------------------------------------------
    # Pwork
    # ------------------------------------------------------------------
    def test_90_il_flusso_non_contatta_pwork(self):
        """Se checked() provasse a inviare, il blocco di sicurezza farebbe fallire qui."""
        viaggio = self._crea_viaggio(
            'TEST-NO-PWORK', self.adesso - timedelta(hours=5), self.adesso - timedelta(hours=2))
        viaggio.checked()
        self.env['account.analytic.line'].overlapping_time_management()
