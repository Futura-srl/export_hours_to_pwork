import hashlib, logging, pytz
from datetime import date, datetime, time, timedelta

from dateutil.relativedelta import relativedelta
from markupsafe import escape

from odoo import api, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TZ = pytz.timezone('Europe/Rome')
PARAMETRO = "export_hours_to_pwork.%s"


class PworkCaricamento(models.AbstractModel):
    """ Caricamento automatico delle ore su Pwork e chiusura del mese.

    Il giorno di un viaggio e' la data (ora italiana) di inizio delle ore pagate, secondo il metodo di
    pagamento, oppure del pianificato se quell'orario non c'e'. Partecipano solo i viaggi dei tipi con la
    spunta "Carica ore su Pwork". I giorni si caricano in ordine, a partire dal primo giorno dopo l'ultimo
    mese chiuso e fino a ieri: un giorno parte solo se tutti i viaggi suoi e del giorno prima sono checked.
    """
    _name = "pwork.caricamento"
    _description = "Caricamento ore su Pwork"

    # ------------------------------------------------------------------
    # date e parametri
    # ------------------------------------------------------------------
    @api.model
    def _oggi(self):
        return datetime.now(TZ).date()

    @api.model
    def _ora(self):
        return datetime.now(TZ).hour

    @api.model
    def _minuti(self):
        return datetime.now(TZ).minute

    @api.model
    def _nuova_gestione_attiva(self):
        """ Con "Usa il metodo precedente" acceso nelle impostazioni Pwork tutto il caricamento automatico
        si ferma e le funzioni manuali tornano a comportarsi come prima """
        return not self._parametro('metodo_precedente')

    @api.model
    def _ultimo_giorno_lavorativo(self, giorno):
        """ Ultimo giorno lavorativo del mese di `giorno`, secondo il calendario di lavoro della societa'
        e le festivita' pubbliche inserite in Odoo """
        calendario = self.env.company.resource_calendar_id
        candidato = giorno.replace(day=1) + relativedelta(months=1) - timedelta(days=1)
        while candidato.month == giorno.month:
            if not calendario:
                return candidato
            inizio = TZ.localize(datetime.combine(candidato, time.min))
            if list(calendario._work_intervals_batch(inizio, inizio + timedelta(days=1), tz=TZ)[False]):
                return candidato
            candidato -= timedelta(days=1)
        return None

    @api.model
    def _giorno_roma(self, momento):
        """ Data italiana di un datetime di Odoo (UTC senza fuso) """
        return pytz.utc.localize(momento).astimezone(TZ).date()

    @api.model
    def _inizio_giorno_utc(self, giorno):
        """ Mezzanotte italiana del giorno, in UTC senza fuso come la salva Odoo """
        return TZ.localize(datetime.combine(giorno, time.min)).astimezone(pytz.utc).replace(tzinfo=None)

    @api.model
    def _descrivi_turno(self, turno):
        inizio = pytz.utc.localize(turno.datetime_start).astimezone(TZ).strftime('%d/%m/%Y %H:%M')
        fine = pytz.utc.localize(turno.datetime_stop).astimezone(TZ).strftime('%d/%m/%Y %H:%M')
        viaggio = turno.gtms_id.name if 'gtms_id' in turno._fields else False
        riferimento = f", timesheet {turno.id}" if 'gtms_id' in turno._fields else ""
        return f"{viaggio or turno.name or ''} ({inizio} - {fine}{riferimento})".strip()

    @api.model
    def _parametro(self, nome, default=False):
        return self.env['ir.config_parameter'].sudo().get_param(PARAMETRO % nome, default)

    @api.model
    def _ultimo_mese_chiuso(self):
        """ Primo giorno dell'ultimo mese chiuso, o None se non e' impostato """
        mese = self._parametro('mese_chiuso')
        anno = self._parametro('anno_chiuso')
        if not mese or not anno:
            return None
        return date(int(anno), int(mese), 1)

    @api.model
    def _primo_giorno_aperto(self):
        """ Primo giorno del mese successivo all'ultimo mese chiuso, o None se non e' impostato """
        ultimo = self._ultimo_mese_chiuso()
        return ultimo + relativedelta(months=1) if ultimo else None

    @api.model
    def _imposta_mese_chiuso(self, primo_giorno_mese):
        parametri = self.env['ir.config_parameter'].sudo()
        parametri.set_param(PARAMETRO % 'mese_chiuso', str(primo_giorno_mese.month))
        parametri.set_param(PARAMETRO % 'anno_chiuso', str(primo_giorno_mese.year))

    # ------------------------------------------------------------------
    # viaggi e timesheet di un periodo
    # ------------------------------------------------------------------
    @api.model
    def _viaggi_aperti(self, data_da, data_a):
        """ Viaggi da caricare (tipo con la spunta) non ancora checked o annullati, con giorno nel periodo.
        Quelli fatti solo da esterni non hanno ore da caricare e non contano """
        inizio = self._inizio_giorno_utc(data_da)
        fine = self._inizio_giorno_utc(data_a + timedelta(days=1))
        viaggi = self.env['gtms.trip'].sudo().search([
            ('trip_type_id.carica_ore_pwork', '=', True),
            ('state', 'not in', ['checked', 'cancel']),
            '|',
            '&', ('first_stop_planned_at', '>=', inizio), ('first_stop_planned_at', '<', fine),
            '&', ('trip_start_from_survey', '>=', inizio), ('trip_start_from_survey', '<', fine),
        ], order="first_stop_planned_at asc, id asc")
        return viaggi.filtered(
            lambda viaggio: viaggio._get_pwork_start()
            and data_da <= self._giorno_roma(viaggio._get_pwork_start()) <= data_a
            and not self._solo_esterni(viaggio))

    @api.model
    def _solo_esterni(self, viaggio):
        """ Viaggio fatto solo da esterni: non ha ore da caricare, quindi aperto non ferma nulla """
        conducenti = viaggio.trip_vehicle_manager_ids.driver_id | viaggio.trip_vehicle_manager_ids.learning_driver_id
        return bool(conducenti) and all(conducenti.mapped('is_esterno'))

    @api.model
    def _timesheet(self, data_da, data_a, dominio=None):
        """ Timesheet dei viaggi da caricare, con inizio (ora italiana) nel periodo """
        return self.env['account.analytic.line'].sudo().search([
            ('gtms_id', '!=', False),
            ('gtms_id.trip_type_id.carica_ore_pwork', '=', True),
            ('datetime_start', '>=', self._inizio_giorno_utc(data_da)),
            ('datetime_start', '<', self._inizio_giorno_utc(data_a + timedelta(days=1))),
        ] + (dominio or []), order="datetime_start asc, id asc")

    # ------------------------------------------------------------------
    # caricamento
    # ------------------------------------------------------------------
    @api.model
    def _carica_giorno(self, giorno, forza=False):
        """ Carica un giorno: sovrapposizioni, validazione, righe Pwork da inviare.

        forza=True (scadenza della chiusura): i viaggi ancora aperti non fermano il giorno e i dipendenti
        con sovrapposizioni da sistemare restano fuori, invece di fermare tutto. """
        esito = {'giorno': giorno, 'viaggi_aperti': self.env['gtms.trip'], 'errori': [], 'validati': 0, 'righe': 0}
        if not forza:
            giorno_precedente = giorno - timedelta(days=1)
            primo_aperto = self._primo_giorno_aperto()
            data_da = max(giorno_precedente, primo_aperto) if primo_aperto else giorno_precedente
            esito['viaggi_aperti'] = self._viaggi_aperti(data_da, giorno)
            if esito['viaggi_aperti']:
                return esito

        bozze = self._timesheet(giorno, giorno, [('validated', '=', False)])
        errori = []
        if bozze:
            with self.env.cr.savepoint(flush=True) as punto:
                errori = bozze._gestisci_sovrapposizioni()
                if errori:
                    punto.rollback()
                    self.env.invalidate_all()
        if errori:
            esito['errori'] = [testo for _dipendente, testo in errori]
            if not forza:
                return esito
            # si tengono fuori i dipendenti con errori e si sistemano gli altri
            con_errori = self.env['hr.employee'].browse(list({dipendente.id for dipendente, _testo in errori}))
            bozze = bozze.filtered(lambda line: line.employee_id not in con_errori)
            bozze._gestisci_sovrapposizioni()

        if bozze:
            bozze.write({'validated': True})
            bozze._update_last_validated_timesheet_date()
            esito['validati'] = len(bozze)

        esito['righe'] = self._elabora_validati(giorno, giorno)
        return esito

    @api.model
    def _elabora_validati(self, data_da, data_a):
        """ Crea le righe Pwork (da inviare in automatico) per i timesheet validati e non ancora elaborati.
        Restituisce quante righe sono state create. """
        da_elaborare = self._timesheet(data_da, data_a, [('validated', '=', True), ('processed', '=', False), ('pwork', '=', False)])
        if not da_elaborare:
            return 0
        Righe = self.env['account.analytic.line.pwork'].sudo()
        righe_prima = Righe.search_count([])
        da_elaborare.write({'processed': True})
        da_elaborare.with_context(pwork_invio_automatico=True).upload_to_pwork_table_2()
        return Righe.search_count([]) - righe_prima

    @api.model
    def _carica_giorni(self, data_a=None, forza=False):
        """ Carica in ordine i giorni dal primo giorno aperto fino a data_a (al massimo ieri).
        Senza forza si ferma al primo giorno che non si puo' caricare. """
        esiti = []
        primo_aperto = self._primo_giorno_aperto()
        if not primo_aperto:
            return esiti
        ieri = self._oggi() - timedelta(days=1)
        ultimo = min(ieri, data_a) if data_a else ieri
        giorno = primo_aperto
        while giorno <= ultimo:
            esito = self._carica_giorno(giorno, forza=forza)
            esiti.append(esito)
            if not forza and (esito['viaggi_aperti'] or esito['errori']):
                break
            giorno += timedelta(days=1)
        return esiti

    # ------------------------------------------------------------------
    # chiusura del mese
    # ------------------------------------------------------------------
    @api.model
    def _controlla_chiusura(self):
        """ Chiude i mesi finiti in cui tutti i viaggi sono checked e tutti i timesheet validati.
        Al giorno di scadenza carica quello che e' pronto e chiude comunque. """
        esiti = []
        if not self._parametro('chiusura_automatica'):
            return esiti
        oggi = self._oggi()
        while True:
            mese = self._primo_giorno_aperto()
            if not mese:
                break
            mese_successivo = mese + relativedelta(months=1)
            fine_mese = mese_successivo - timedelta(days=1)
            if fine_mese >= oggi:
                break
            viaggi_aperti = self._viaggi_aperti(mese, fine_mese)
            bozze = self._timesheet(mese, fine_mese, [('validated', '=', False)])
            if not viaggi_aperti and not bozze:
                # i validati non ancora elaborati vanno comunque su Pwork
                self._elabora_validati(mese, fine_mese)
                self._imposta_mese_chiuso(mese)
                esiti.append({'mese': mese, 'scadenza': False, 'viaggi_aperti': viaggi_aperti, 'bozze': bozze, 'caricamento': []})
                continue
            giorno_scadenza = int(self._parametro('giorno_scadenza_chiusura', 0) or 0)
            if giorno_scadenza:
                ultimo_giorno_mese_successivo = (mese_successivo + relativedelta(months=1) - timedelta(days=1)).day
                scadenza = mese_successivo.replace(day=min(giorno_scadenza, ultimo_giorno_mese_successivo))
                if oggi >= scadenza:
                    caricamento = self._carica_giorni(data_a=fine_mese, forza=True)
                    self._imposta_mese_chiuso(mese)
                    esiti.append({
                        'mese': mese, 'scadenza': True, 'caricamento': caricamento,
                        'viaggi_aperti': self._viaggi_aperti(mese, fine_mese),
                        'bozze': self._timesheet(mese, fine_mese, [('validated', '=', False)]),
                    })
                    continue
            break
        return esiti

    # ------------------------------------------------------------------
    # invio
    # ------------------------------------------------------------------
    @api.model
    def _invia_righe_automatiche(self):
        """ Invia a Pwork le righe create dal caricamento automatico e mai inviate """
        righe = self.env['account.analytic.line.pwork'].sudo().search(
            [('invio_automatico', '=', True), ('pwork', '=', False), ('error_txt', '=', False)],
            order="datetime_start asc, id asc")
        esito = {'da_inviare': len(righe), 'inviate': 0, 'errori': []}
        if not righe:
            return esito
        if self.env['ir.config_parameter'].sudo().get_param('database.is_neutralized'):
            esito['errori'].append("Database neutralizzato (copia di test): invio a Pwork non eseguito")
            return esito
        try:
            self.env['res.config.settings'].sudo().get_token_from_pwork()
        except Exception as e:
            _logger.exception("Token Pwork non ottenuto")
            esito['errori'].append(f"Token Pwork non ottenuto, invio rimandato: {e}")
            return esito
        righe.upload_to_pwork()
        for riga in righe:
            if riga.pwork:
                esito['inviate'] += 1
            elif riga.error_txt:
                esito['errori'].append(f"{riga.employee_id.name} {self._descrivi_turno(riga)}: {riga.error_txt}")
        return esito

    # ------------------------------------------------------------------
    # cron e rapporto
    # ------------------------------------------------------------------
    @api.model
    def _cron_caricamento(self):
        if not self._nuova_gestione_attiva():
            return
        caricamento = []
        if self._parametro('caricamento_automatico'):
            caricamento = self._carica_giorni()
        chiusure = self._controlla_chiusura()
        invio = self._invia_righe_automatiche()
        self._invia_rapporto(caricamento, chiusure, invio)

    @api.model
    def _esegui_manuale(self, data_a):
        """ Caricamento lanciato a mano: carica e controlla la chiusura, l'invio lo fa il cron subito dopo """
        if not self._nuova_gestione_attiva():
            raise UserError(_("Nelle impostazioni Pwork è attivo il metodo precedente: il caricamento automatico è disattivato."))
        caricamento = self._carica_giorni(data_a=data_a)
        chiusure = self._controlla_chiusura()
        self.env.ref('export_hours_to_pwork.ir_cron_pwork_caricamento_ore').sudo()._trigger()
        testo = self._testo_rapporto(caricamento, chiusure, None)
        self._invia_rapporto(caricamento, chiusure, None)
        return testo

    @api.model
    def _testo_rapporto(self, caricamento, chiusure, invio):
        movimenti, problemi = self._righe_rapporto(caricamento, chiusure, invio)
        return "\n".join(movimenti + problemi)

    @api.model
    def _righe_rapporto(self, caricamento, chiusure, invio):
        """ Divide il rapporto tra quello che e' successo (movimenti) e quello che resta bloccato (problemi):
        i problemi che non cambiano non fanno partire una nuova mail """
        movimenti = []
        problemi = []
        righe = problemi
        if not self._primo_giorno_aperto():
            problemi.append("Ultimo mese chiuso non impostato nelle impostazioni Pwork: nessun giorno caricato.")
        # i mesi chiusi alla scadenza nello stesso giro hanno il loro riepilogo: il "fermo" non vale piu'
        mesi_scaduti = {(chiusura['mese'].year, chiusura['mese'].month) for chiusura in chiusure if chiusura['scadenza']}
        for esito in caricamento:
            giorno = esito['giorno'].strftime('%d/%m/%Y')
            if (esito['giorno'].year, esito['giorno'].month) in mesi_scaduti:
                esito = dict(esito, viaggi_aperti=[], errori=[])
            if esito['viaggi_aperti']:
                problemi.append(f"Caricamento fermo al {giorno}: viaggi non ancora checked:")
                problemi += [f"  - {viaggio.name} ({self._giorno_roma(viaggio._get_pwork_start()).strftime('%d/%m/%Y')}, {viaggio.current_driver_id.name or 'senza autista'})"
                             for viaggio in esito['viaggi_aperti'][:50]]
            if esito['errori']:
                problemi.append(f"{giorno}: turni sovrapposti da sistemare a mano:")
                problemi += [f"  - {errore}" for errore in esito['errori']]
            if esito['validati'] or esito['righe']:
                movimenti.append(f"{giorno}: {esito['validati']} timesheet validati, {esito['righe']} righe Pwork create")
        for chiusura in chiusure:
            mese = chiusura['mese'].strftime('%m/%Y')
            if chiusura['scadenza']:
                validati = sum(esito['validati'] for esito in chiusura['caricamento'])
                create = sum(esito['righe'] for esito in chiusura['caricamento'])
                movimenti.append(f"Mese {mese} chiuso alla scadenza: caricati {validati} timesheet ({create} righe Pwork); restano fuori {len(chiusura['viaggi_aperti'])} viaggi non checked e {len(chiusura['bozze'])} timesheet in bozza")
                movimenti += [f"  - viaggio {viaggio.name}" for viaggio in chiusura['viaggi_aperti'][:50]]
                for esito in chiusura['caricamento']:
                    movimenti += [f"  - {esito['giorno'].strftime('%d/%m/%Y')}: {errore}" for errore in esito['errori']]
            else:
                movimenti.append(f"Mese {mese} chiuso: tutti i viaggi checked e tutti i timesheet validati")
        if invio and invio['inviate']:
            movimenti.append(f"Invio a Pwork: {invio['inviate']} righe inviate su {invio['da_inviare']}")
        if invio and invio['errori']:
            problemi.append("Invio a Pwork non riuscito:")
            problemi += [f"  - {errore}" for errore in invio['errori']]
        return movimenti, problemi

    @api.model
    def _invia_rapporto(self, caricamento, chiusure, invio):
        movimenti, problemi = self._righe_rapporto(caricamento, chiusure, invio)
        if not movimenti and not problemi:
            return
        testo = "\n".join(movimenti + problemi)
        _logger.info("Rapporto caricamento Pwork:\n%s", testo)
        # i problemi che restano uguali (es. lo stesso giorno fermo) non fanno partire un'altra mail:
        # la mail parte quando e' successo qualcosa di nuovo o quando i problemi cambiano
        impronta = hashlib.sha1("\n".join(problemi).encode()).hexdigest()
        problemi_nuovi = self._parametro('ultimo_rapporto') != impronta
        self.env['ir.config_parameter'].sudo().set_param(PARAMETRO % 'ultimo_rapporto', impronta)
        if not movimenti and not problemi_nuovi:
            return
        self._invia_mail('email_avvisi', "Pwork: caricamento ore e chiusura mese", testo)

    @api.model
    def _invia_mail(self, parametro_email, oggetto, testo):
        destinatario = self._parametro(parametro_email)
        if not destinatario:
            return False
        return self._crea_mail({
            'email_to': destinatario,
            'subject': oggetto,
            'body_html': f"<pre>{escape(testo)}</pre>",
        })

    @api.model
    def _crea_mail(self, valori):
        # Sulle copie (database neutralizzato) la mail si crea gia' annullata: si legge in Impostazioni ->
        # Tecnico -> Email, ma la coda di invio non la prende mai
        if self.env['ir.config_parameter'].sudo().get_param('database.is_neutralized'):
            _logger.info("Database neutralizzato: mail '%s' a %s creata annullata, non verra' inviata", valori.get('subject'), valori.get('email_to'))
            valori = dict(valori, state='cancel')
        return self.env['mail.mail'].sudo().create(valori)

    # ------------------------------------------------------------------
    # controllo HR: contratti senza badge, autisti senza contratto
    # ------------------------------------------------------------------
    @api.model
    def _cron_controllo_hr(self):
        """ Il lunedi' controlla la settimana precedente. L'ultimo giorno lavorativo del mese controlla il
        mese intero, compresi i viaggi gia' pianificati nei giorni che mancano: la mail arriva dalle 7, cosi'
        c'e' il tempo di sistemare prima della chiusura """
        if not self._nuova_gestione_attiva():
            return
        oggi = self._oggi()
        if self._parametro('ultimo_controllo_hr') == str(oggi) or self._ora() < 7:
            return
        periodi = []
        if oggi.weekday() == 0:
            periodi.append(("della settimana", oggi - timedelta(days=7), oggi - timedelta(days=1)))
        if oggi == self._ultimo_giorno_lavorativo(oggi):
            primo_del_mese = oggi.replace(day=1)
            periodi.append(("del mese", primo_del_mese, primo_del_mese + relativedelta(months=1) - timedelta(days=1)))
        if not periodi:
            return
        self.env['ir.config_parameter'].sudo().set_param(PARAMETRO % 'ultimo_controllo_hr', str(oggi))
        sezioni = []
        for nome, data_da, data_a in periodi:
            anomalie = self._controllo_hr(data_da, data_a)
            if anomalie:
                sezioni.append(f"Controllo {nome} dal {data_da.strftime('%d/%m/%Y')} al {data_a.strftime('%d/%m/%Y')}:\n" + "\n".join(anomalie))
        if sezioni:
            self._invia_mail('email_hr', "Pwork: autisti con giornate di viaggio senza badge", "\n\n".join(sezioni))

    @api.model
    def _controllo_hr(self, data_da, data_a):
        """ Autisti e allievi dipendenti con viaggi nel periodo: giornate con viaggi senza un badge valido sul
        contratto, cioe' le ore che all'invio a Pwork darebbero "Badge mancante". Gli esterni non hanno badge;
        i giorni senza contratto non si contano (li blocca gia' il checked). """
        anomalie = []

        # giornate con viaggi da caricare per ogni autista e allievo
        viaggi = self.env['gtms.trip'].sudo().search([
            ('trip_type_id.carica_ore_pwork', '=', True),
            ('state', '!=', 'cancel'),
            '|',
            '&', ('first_stop_planned_at', '>=', self._inizio_giorno_utc(data_da)),
                 ('first_stop_planned_at', '<', self._inizio_giorno_utc(data_a + timedelta(days=1))),
            '&', ('trip_start_from_survey', '>=', self._inizio_giorno_utc(data_da)),
                 ('trip_start_from_survey', '<', self._inizio_giorno_utc(data_a + timedelta(days=1))),
        ])
        giornate = {}
        for viaggio in viaggi:
            inizio_viaggio = viaggio._get_pwork_start()
            if not inizio_viaggio or not data_da <= self._giorno_roma(inizio_viaggio) <= data_a:
                continue
            for autista in viaggio.trip_vehicle_manager_ids.driver_id | viaggio.trip_vehicle_manager_ids.learning_driver_id:
                if autista.is_esterno:
                    continue
                giornate.setdefault(autista, set()).add(self._giorno_roma(inizio_viaggio))

        for autista in sorted(giornate, key=lambda partner: partner.name or ''):
            dipendenti = self.env['hr.employee'].sudo().with_context(active_test=False).search([('address_home_id', '=', autista.id)])
            contratti = self.env['hr.contract'].sudo().search([('employee_id', 'in', dipendenti.ids)])
            badge_per_contratto = {
                contratto.id: self.env['hr.badgespwork'].sudo().search([('contract_ids', '=', contratto.id), ('active', '=', True)])
                for contratto in contratti
            }
            senza_badge = []
            for giorno in sorted(giornate[autista]):
                # stesse regole del checked (contratto) e dell'invio a Pwork (badge del contratto)
                validi = contratti.filtered(lambda c: c.date_start <= giorno and (not c.date_end or c.date_end >= giorno))
                if not validi:
                    continue
                mezzanotte = datetime.combine(giorno, time.min)
                if not any(b.valid_from and b.valid_from <= mezzanotte and (not b.valid_to or b.valid_to >= mezzanotte)
                           for contratto in validi for b in badge_per_contratto[contratto.id]):
                    senza_badge.append(giorno)
            if senza_badge:
                anomalie.append(f"- {autista.name}: senza badge valido {self._intervalli(senza_badge)}")

        # righe Pwork ferme per badge mancante
        primo_aperto = self._primo_giorno_aperto() or data_da
        righe = self.env['account.analytic.line.pwork'].sudo().search([
            ('pwork', '=', False), ('error_txt', '=', 'Badge mancante'),
            ('datetime_start', '>=', self._inizio_giorno_utc(primo_aperto)),
        ])
        anomalie += [f"- {riga.employee_id.name}: riga Pwork {self._descrivi_turno(riga)} non inviata per badge mancante" for riga in righe]
        return anomalie

    # ------------------------------------------------------------------
    # promemoria ai ROP: viaggi ancora da chiudere
    # ------------------------------------------------------------------
    @api.model
    def _cron_promemoria_rop(self):
        """ Ogni mattina dalle 08:30: a ogni centro di costo i suoi viaggi degli ultimi 30 giorni non ancora
        checked. Sostituisce l'azione pianificata creata a mano in produzione. """
        if not self._parametro('promemoria_rop'):
            return
        oggi = self._oggi()
        if self._parametro('ultimo_promemoria_rop') == str(oggi) or (self._ora(), self._minuti()) < (8, 30):
            return
        self.env['ir.config_parameter'].sudo().set_param(PARAMETRO % 'ultimo_promemoria_rop', str(oggi))
        self._invia_promemoria_rop(oggi)

    @api.model
    def _invia_promemoria_rop(self, oggi):
        viaggi = self.env['gtms.trip'].sudo().search([
            ('first_stop_planned_at', '>=', self._inizio_giorno_utc(oggi - timedelta(days=30))),
            ('first_stop_planned_at', '<', self._inizio_giorno_utc(oggi)),
            ('trip_type_id.carica_ore_pwork', '=', True),
            ('state', 'not in', ['checked', 'cancel']),
        ], order="first_stop_planned_at asc, id asc")
        Team = self.env['helpdesk.team'].sudo()
        mail = self.env['mail.mail']
        for tipo in viaggi.trip_type_id:
            organizzazione = tipo.organization_id
            team = Team.search([('organization_id', '=', organizzazione.id)], limit=1) if organizzazione else Team
            indirizzi = [email for email in team.message_follower_ids.mapped('partner_id.email') if email]
            if not indirizzi:
                continue
            per_giorno = {}
            for viaggio in viaggi.filtered(lambda v: v.trip_type_id == tipo):
                per_giorno.setdefault(self._giorno_roma(viaggio.first_stop_planned_at), []).append(viaggio)

            corpo = "Buongiorno,<br/>Di seguito la lista dei viaggi che sono ancora da chiudere:<br/><br/>"
            for giorno in sorted(per_giorno):
                corpo += f"{giorno.strftime('%d/%m/%Y')}<br/>"
                for viaggio in per_giorno[giorno]:
                    autisti = set()
                    for riga in viaggio.trip_vehicle_manager_ids.filtered('driver_id'):
                        if riga.learning_driver_id:
                            autisti.add(f"{riga.driver_id.name} - Affiancato: {riga.learning_driver_id.name}")
                        else:
                            autisti.add(riga.driver_id.name)
                    testo_autisti = ", ".join(sorted(autisti)) if autisti else "NON ASSEGNATO"
                    corpo += f"- {escape(viaggio.name)} | Autista: {escape(testo_autisti)}<br/>"
                corpo += "<br/>"
            corpo += "Vi ricordiamo che i viaggi devono essere chiusi entro il giorno lavorativo seguente.<br/><br/>"
            corpo += f"Grazie,<br/>{escape(self.env.company.name)}"

            valori = {
                'subject': f"Viaggi ancora da chiudere - {organizzazione.name}",
                'body_html': corpo,
                'email_to': ",".join(indirizzi),
                'email_cc': self._parametro('email_hr') or False,
                'reply_to': ",".join(e for e in (self._parametro('email_avvisi'), self._parametro('email_hr')) if e) or False,
            }
            if self._parametro('email_mittente'):
                valori['email_from'] = self._parametro('email_mittente')
            mail |= self._crea_mail(valori)
        return mail

    @api.model
    def _intervalli(self, giorni):
        """ [1, 2, 3, 5] -> 'dal 01/03 al 03/03, il 05/03' """
        parti = []
        inizio = precedente = giorni[0]
        for giorno in giorni[1:] + [None]:
            if giorno and giorno == precedente + timedelta(days=1):
                precedente = giorno
                continue
            if inizio == precedente:
                parti.append(f"il {inizio.strftime('%d/%m/%Y')}")
            else:
                parti.append(f"dal {inizio.strftime('%d/%m/%Y')} al {precedente.strftime('%d/%m/%Y')}")
            if giorno:
                inizio = precedente = giorno
        return ", ".join(parti)
