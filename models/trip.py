import logging, datetime
from odoo import api, fields, models, http, _, Command
from odoo.exceptions import UserError, ValidationError
from datetime import datetime as dt


_logger = logging.getLogger(__name__)
now = datetime.datetime.now()


class Trip(models.Model):
    _inherit = "gtms.trip"

    def _states_list(self):
        states = self.env['gtms.trip.states'].search([])
        return states.mapped(lambda s: (s.name, s.description))


    check = fields.Boolean(default=False)
    trip_start_from_survey = fields.Datetime()
    trip_end_from_survey = fields.Datetime()
    drivers_payment = fields.Selection([('ore_pianificate','Ore pianificate'),('ore_effettive','Ore effettive'),('ore_macarena','Ore Mix 1'),('ore_macarena_inverso','Ore Mix inverso'),('non_pagabile','Non pagare')], store=True, index=True)
    all_drivers_ids = fields.One2many('res.partner', compute="_find_all_drivers_ids", stored=True, index=True)

    state = fields.Selection(_states_list,
                             string='Status', readonly=True, copy=False, index=True,
                             default='draft', compute='_compute_operations_data', store=True, tracking=True)

    total_hour_payment = fields.Float(string="Totale ore pagate", compute="_compute_total_hour_payment")

    @api.constrains('trip_start_from_survey', 'trip_end_from_survey')
    def _check_survey_trip_dates(self):
        """ Controlla che l'orario di fine viaggio non sia precedente a quello di inizio viaggio """
        for record in self:
            if record.state != 'done':
                continue
            else:
                if record.trip_start_from_survey and record.trip_end_from_survey:
                    if record.trip_start_from_survey > record.trip_end_from_survey:
                        raise ValidationError(_("L'orario del sondaggio di fine viaggio non può essere precedente a quello di inizio."))

    def _compute_total_hour_payment(self):
        """ Calcola il totale delle ore pagate in base al metodo scelto """
        for record in self:
            if record.drivers_payment == 'ore_pianificate':
                if record.first_stop_planned_at and record.last_stop_planned_at:
                    start_time = record.first_stop_planned_at
                    end_time = record.last_stop_planned_at
                    work_time = end_time - start_time
                    working_seconds = work_time.total_seconds() / 3600.0
                    record.total_hour_payment = working_seconds
                else:
                    record.total_hour_payment = 0
            elif record.drivers_payment == 'ore_effettive':
                if record.trip_start_from_survey and record.trip_end_from_survey:
                    start_time = record.trip_start_from_survey
                    end_time = record.trip_end_from_survey
                    work_time = end_time - start_time
                    working_seconds = work_time.total_seconds() / 3600.0
                    record.total_hour_payment = working_seconds
                else:
                    record.total_hour_payment = 0
            elif record.drivers_payment == 'ore_macarena':
                if record.first_stop_planned_at and record.trip_end_from_survey:
                    start_time = record.first_stop_planned_at
                    end_time = record.trip_end_from_survey
                    work_time = end_time - start_time
                    working_seconds = work_time.total_seconds() / 3600.0
                    record.total_hour_payment = working_seconds
                else:
                    record.total_hour_payment = 0
            elif record.drivers_payment == 'ore_macarena_inverso':
                if record.trip_start_from_survey and record.last_stop_planned_at:
                    start_time = record.trip_start_from_survey
                    end_time = record.last_stop_planned_at
                    work_time = end_time - start_time
                    working_seconds = work_time.total_seconds() / 3600.0
                    record.total_hour_payment = working_seconds
                else:
                    record.total_hour_payment = 0
            elif record.drivers_payment == 'non_pagabile':
                record.total_hour_payment = 0
            else:
                record.total_hour_payment = 0


    def get_drivers_payment(self):
        """ Recupera il metodo di pagamento predefinito dal tipo di viaggio e lo assegna al viaggio se non è già impostato."""
        # _logger.info("Avvio get_default_drivers_payment")
        trips = self.env['gtms.trip'].search([('drivers_payment', '=', False)])
        for record in trips:
            # se il record e' stato creato con data <= al 1 luglio 2025 oppure ha gia' un metodo di pagamento impostato, salto
            # config = self.env['ir.config_parameter'].sudo()
            # date_controllo_pagamenti = config.get_param('export_hours_to_pwork.date_controllo_viaggi')

            if record.create_date <= datetime.datetime(2026, 3, 1) or record.drivers_payment != False:
                continue
            else:
                # _logger.info(f"Record: {record}")
                # _logger.info(f"self.drivers_payment: {record.drivers_payment}")
                record.drivers_payment = record.trip_type_id.default_drivers_payment

    # # Creo una funzione che gestisce il metodo di pagamento alla creazione del viaggio
    # @api.onchange('trip_type_id')
    # def get_drivers_payment(self):
    #     for record in self:
    #         if record.trip_type_id and record.drivers_payment == False:
    #             record.drivers_payment = record.trip_type_id.default_drivers_payment

    def _check_overlapping_trips(self):
        """ Controlla se ci sono viaggi con autisti in comune e orari che si sovrappongono."""
        for record in self:
            start_time, end_time = record._get_trip_interval()
            if not start_time or not end_time:
                continue

            # Cerco solo viaggi "checked" degli stessi driver che
            # hanno intervalli potenzialmente sovrapposti
            other_trips = self.env['gtms.trip'].search([
                ('id', '!=', record.id),
                ('state', '=', 'checked'),
                ('all_drivers_ids', 'in', record.all_drivers_ids.ids),
                '|',
                '&', ('first_stop_planned_at', '<=', end_time), ('last_stop_planned_at', '>=', start_time),
                '&', ('trip_start_from_survey', '<=', end_time), ('trip_end_from_survey', '>=', start_time),
            ])

            overlapping = []

            current_drivers = record.all_drivers_ids  # driver del viaggio corrente

            for trip in other_trips:
                other_start, other_end = trip._get_trip_interval()
                if not other_start or not other_end:
                    continue
                # controllo sovrapposizione temporale
                if other_start <= end_time and other_end >= start_time:
                    # CONSENTITO solo se:
                    # 1. il viaggio corrente inizia dentro l'altro e finisce dopo
                    # 2. il viaggio corrente inizia prima e finisce dentro l'altro
                    allowed_case = (
                            (start_time >= other_start and end_time > other_end) or
                            (start_time < other_start and end_time <= other_end)
                    )

                    if not allowed_case:
                        # controllo driver in comune
                        common_drivers = current_drivers & trip.all_drivers_ids
                        if common_drivers:
                            # conversione datetimes in timezone utente
                            other_start_user = fields.Datetime.context_timestamp(self, other_start)
                            other_end_user = fields.Datetime.context_timestamp(self, other_end)
                            drivers = ', '.join(common_drivers.mapped('name'))
                            label = "Autista in conflitto" if len(common_drivers) == 1 else "Autisti in conflitto"
                            label2 = "un autista risulta già assegnato" if len(
                                common_drivers) == 1 else "alcuni autisti risultano già assegnati"
                            overlapping.append(
                                f"Viaggio: {trip.name}\nIntervallo: {other_start_user.strftime('%d/%m/%Y %H:%M')} - {other_end_user.strftime('%d/%m/%Y %H:%M')}\n{label}: {drivers}"
                            )

            if overlapping:
                raise ValidationError(_(
                    f"Non puoi mettere il viaggio {record.name} in stato 'checked' perché {label2} ad un altro viaggio nello stesso orario:\n"
                    + "\n".join(overlapping)
                ))

    def _get_trip_interval(self):
        """ Restituisce (start_time, end_time) in base al drivers_payment """
        self.ensure_one()
        if self.drivers_payment == 'ore_pianificate':
            return self.first_stop_planned_at, self.last_stop_planned_at
        elif self.drivers_payment == 'ore_effettive':
            return self.trip_start_from_survey, self.trip_end_from_survey
        elif self.drivers_payment == 'ore_macarena':
            return self.first_stop_planned_at, self.trip_end_from_survey
        elif self.drivers_payment == 'ore_macarena_inverso':
            return self.trip_start_from_survey, self.last_stop_planned_at
        return None, None


    # @api.depends('trip_vehicle_manager_ids')
    # def _find_all_drivers_ids(self):
    #     for field in self:
    #         drivers = []
    #         for trip in self:
    #             trip.all_drivers_ids = False
    #         # Cerco tutti i record della tabella gtms.trip.vehicle.manager associati al viaggio
    #         data = self.env['gtms.trip.vehicle.manager'].search_read([('trip_id', '=', field.id)],['driver_id','learning_driver_id'])
    #         for record in data:
    #             if record['driver_id'] != False:
    #                 driver_1 = record['driver_id'][0]
    #                 drivers.append(driver_1)
    #             if record['learning_driver_id'] != False:
    #                 driver_2 = record['learning_driver_id'][0]
    #                 drivers.append(driver_2)
    #         field.all_drivers_ids = list(set(drivers))

    @api.depends('trip_vehicle_manager_ids')
    def _find_all_drivers_ids(self):
        for trip in self:
            drivers = []

            data = self.env['gtms.trip.vehicle.manager'].search_read(
                [('trip_id', '=', trip.id)],
                ['driver_id', 'learning_driver_id']
            )

            for record in data:
                if record['driver_id']:
                    drivers.append(record['driver_id'][0])
                if record['learning_driver_id']:
                    drivers.append(record['learning_driver_id'][0])
            if drivers:
                trip.all_drivers_ids = list(set(drivers))
            else:
                trip.all_drivers_ids = False



    # @api.depends('check','number_of_operations_executed', 'number_of_operation_running',
    #              'number_of_operation_planned', 'number_operation_partially_planned', 'number_of_operations_cancelled',
    #              'is_ready', 'number_of_operations', 'is_canceled')
    # def _compute_state(self):
    #     for trip in self:
    #         if trip.number_of_operations != 0 and (trip.number_of_operations == trip.number_of_operations_executed) and trip.check == False:
    #             trip.is_readonly = False
    #             trip.state = 'done'
    #             trip.check = False
    #         elif trip.is_canceled and trip.check == False:
    #             trip.state = 'cancel'
    #             trip.check = False
    #         elif not trip.is_ready and trip.check == False:
    #             trip.state = 'draft'
    #             trip.check = False
    #         elif trip.number_of_operation_running > 0 or trip.number_of_operations_executed > 0 and trip.check == False:
    #             trip.state = 'running'
    #             trip.check = False
    #         elif trip.number_of_operations != 0 and (trip.number_of_operations == trip.number_of_operation_planned) and trip.check == False:
    #             trip.state = 'planned'
    #             trip.check = False
    #         elif trip.number_of_operation_planned > 0 and trip.check == False:
    #             trip.state = 'partially'
    #             trip.check = False
    #         elif trip.number_operation_partially_planned > 0 and trip.check == False:
    #             trip.state = 'planning'
    #             trip.check = False
    #         elif trip.is_ready == True and trip.check == False:
    #             trip.state = 'ready'
    #             trip.check = False
    #         elif trip.check == True:
    #             trip.state = 'checked'
    #             trip.is_readonly = True
    #         else:
    #             trip.state = 'draft'
    #             trip.check = False

    @api.depends('check','number_of_operations_executed', 'number_of_operation_running',
                 'number_of_operation_planned', 'number_operation_partially_planned', 'number_of_operations_cancelled',
                 'is_ready', 'number_of_operations', 'is_canceled')
    def _compute_operations_data(self):
        # Chiamata al metodo originale per mantenere le funzionalità esistenti
        super(Trip, self)._compute_operations_data()

        # Aggiungi qui le tue operazioni aggiuntive
        for trip in self:
            # Esempio: Se il tuo campo personalizzato è True, imposta lo stato su 'personalizzato'
            if trip.check == True:
                trip.state = 'checked'
                trip.is_readonly = True


    def unchecked(self):
        for record in self:
            # Cerco gli orari inseriti nel Timesheet prima di rimuoverli
            work_times = self.env['account.analytic.line'].sudo().search([('gtms_id', '=', record.id)])

            # Controllo se ci sono orari già convalidati
            if any(work_time.validated_status in ['validated','processed','done'] for work_time in work_times):
                raise ValidationError(_("Il viaggio contiene degli orari già convalidati su Pwork"))

            # Rimuovo gli orari dal timesheet
            for work_time in work_times:
                ore = int(work_time.unit_amount)
                minuti = round((work_time.unit_amount - ore) * 60)
                message = f"Ho eliminato il timesheet con ID: {work_time.id} per il dipendente {work_time.employee_id.name} (ID: {work_time.employee_id.id}) relativo al viaggio {work_time.gtms_id.name} (ID Viaggio: {work_time.gtms_id.id}) con orario di inizio {work_time.datetime_start} e orario di fine {work_time.datetime_stop}, per un totale di {ore:02d}:{minuti:02d} ore."
                record.message_post(body=message, subtype_xmlid="mail.mt_note")
                work_time.unlink()
            record.check = False


    def checked(self):
        """Effettua tutti i controlli e imposta il viaggio sullo stato checked. Inoltre esporta le ore sul timesheet in base al metodo di pagamento scelto."""
        for record in self:
            if record.state == 'checked':
                continue

            id = record.id
            trip = record.name
            trip_type_id = record.trip_type_id.id
            task_id = self.env['gtms.trip.type'].sudo().search_read([('id', '=', trip_type_id)], ['task_id'])[0]['task_id'][0]
            project_id = self.env['project.task'].sudo().search_read([('id', '=', task_id)], ['project_id'])[0]['project_id'][0]

            _logger.info(record)
            _logger.info(record.state)
            _logger.info(trip_type_id)
            _logger.info(task_id)
            _logger.info(project_id)
            # if record.state == 'checked':
            #     raise ValidationError(_(f"Il viaggio {trip} con id {id} è già sullo stato CHECKED"))
            # if record.state != 'done':
            #     raise ValidationError(_(f"Il viaggio {trip} con id {id} deve prima essere eseguito"))
            driver_id = 0
            laerning_driver_id = 0

            # Controllo che il viaggio abbia i sondaggi chiusi
            surveys = record.survey_input_ids
            for survey in surveys:
                if survey.state != 'done':
                    raise UserError(_("Il viaggio disponde ancora dei sondaggi/ispezioni in stato aperto."))

            # Controllo che le ore messe in pagamento non superino le 18 ore
            if (record.drivers_payment != False or record.drivers_payment != "non_pagabile") and record.total_hour_payment >= 18:
                raise UserError(_("Le ore messe in pagamento sono uguali o superiori a 18 ore."))


            company_id = record.company_ids[0].id
            driver_payment = record.drivers_payment
            if driver_payment == False or driver_payment == False:
                raise ValidationError(_(f"Il viaggio {trip} con id {id} non dispone del metodo di pagamento per i driver"))
            _logger.info(driver_id)
            _logger.info(company_id)
            _logger.info(trip)

            # Raccolgo il datetime in base al metodo scelto
            if driver_payment == "ore_effettive":
                if record.trip_start_from_survey == False or record.trip_end_from_survey == False:
                    raise ValidationError(_(f"Il viaggio {trip} con id {id} non dispone degli orari 'Pianificato'"))
                start_time = record.trip_start_from_survey.date()
                start_datetime = record.trip_start_from_survey
                end_time = record.trip_end_from_survey.date()
                end_datetime = record.trip_end_from_survey
                trip_start = record.trip_start_from_survey
                trip_end = record.trip_end_from_survey
            elif driver_payment == "ore_pianificate":
                if record.first_stop_planned_at == False or record.last_stop_planned_at == False:
                    raise ValidationError(_(f"Il viaggio {trip} con id {id} non dispone degli orari 'Sondaggio'"))
                start_time = record.first_stop_planned_at.date()
                start_datetime = record.first_stop_planned_at
                end_time = record.last_stop_planned_at.date()
                end_datetime = record.last_stop_planned_at
                trip_start = record.first_stop_planned_at
                trip_end = record.last_stop_planned_at
            elif driver_payment == "ore_macarena": # SAREBBE 'ORE MIX', PARTENZA DA ORARIO PIANIFICATO E ARRIVO DA ORARIO EFFETTIVO
                if record.first_stop_planned_at == False or record.last_stop_planned_at == False:
                    raise ValidationError(_(f"Il viaggio {trip} con id {id} non dispone degli orari 'Sondaggio'"))
                start_time = record.first_stop_planned_at.date()
                start_datetime = record.first_stop_planned_at
                end_time = record.trip_end_from_survey.date()
                end_datetime = record.trip_end_from_survey
                trip_start = record.first_stop_planned_at
                trip_end = record.trip_end_from_survey
            elif driver_payment == "ore_macarena_inverso": # SAREBBE 'ORE MIX INVERSO', PARTENZA DA ORARIO EFFETTIVO E ARRIVO DA ORARIO PIANIFICATO
                if record.first_stop_planned_at == False or record.last_stop_planned_at == False:
                    raise ValidationError(_(f"Il viaggio {trip} con id {id} non dispone degli orari 'Sondaggio'"))
                start_time = record.trip_start_from_survey.date()
                start_datetime = record.trip_start_from_survey
                end_time = record.last_stop_planned_at.date()
                end_datetime = record.last_stop_planned_at
                trip_start = record.trip_start_from_survey
                trip_end = record.last_stop_planned_at
            elif driver_payment == "non_pagabile":
                record.check = True
                continue

            # Controllo che esistano driver e veicoli
            if not record.current_fleet_id:
                raise ValidationError(_(f"Il viaggio non dispone di un veicolo associato"))
            if not record.current_driver_id:
                raise ValidationError(_(f"Il viaggio non dispone di un autista associato"))

            record._check_overlapping_trips()

            # Facciol un controllo per evitare che l'orario di fine viaggio sia precedente a quello di inizio
            if start_time > end_time:
                raise ValidationError(_(f"L'orario di fine viaggio non può essere precedente a quello di inizio. Viaggio: {trip}"))

            work_time = end_datetime - start_datetime
            working_seconds = work_time.total_seconds() / 3600.0

            # Cerco gli autisti che hanno guidato durante il viaggio
            drivers = self.env['gtms.trip.vehicle.manager'].sudo().search_read([('trip_id', '=', id)],['driver_id', 'learning_driver_id'])
            # drivers = list({tuple(driver.items()) for driver in drivers})
            for driver in drivers:
                driver_id = driver['driver_id'][0]
                if driver['learning_driver_id']:
                    learning_driver_id = driver['learning_driver_id'][0]
                _logger.info(driver)
                _logger.info(driver['driver_id'])
                _logger.info(driver['learning_driver_id'])


            # Cerco il dipendente con contratto attivo al momento della partenza del viaggio
            employees = self.env['hr.employee'].sudo().search([('address_home_id', '=', driver_id), ('contract_id', '!=', False), '|', ('active', '=', False),('active', '=', True)])
            if driver['learning_driver_id']:
                employees_learning = self.env['hr.employee'].sudo().search([('address_home_id', '=', learning_driver_id), ('contract_id', '!=', False), '|', ('active', '=', False),('active', '=', True)])
                _logger.info(employees_learning)
            _logger.info(employees)
            # Utilizzo indice per essere certo di aver controllato tutti i dipendenti associati al res.partner e nel caso non ci fossero contratti attivi eseguo l'errore
            _logger.info("Setto indice = 0")
            indice = 0
            timesheet = False
            for employee in employees:
                if timesheet != False:
                    continue
                indice = 1 + indice
                _logger.info(f"Indice = {indice}, len = {len(employees)}")
                contracts = self.env['hr.contract'].sudo().search([
                    ('employee_id', '=', employee.id),
                    ('date_start', '<=', start_time),
                    '|', ('date_end', '>=', end_time), ('date_end', '=', False),
                ])
                _logger.info("XXXXXXXXXXXXX")
                _logger.info(contracts)

                if contracts:
                    _logger.info(contracts[0].employee_id.id)
                    employee_id = contracts[0].employee_id.id


                    # Creo il Timesheet
                    timesheet = self.env['account.analytic.line'].sudo().create(
                        {
                            'date': start_time,
                            'project_id': project_id,
                            'task_id': task_id,
                            'employee_id': employee_id,
                            'datetime_start': start_datetime,
                            'datetime_stop': end_datetime,
                            # 'unit_amount': working_seconds,
                            'name': trip,
                            'gtms_id': id,
                        })
                    # self.is_readonly = True
                    ore = int(working_seconds)
                    minuti = round((working_seconds - ore) * 60)
                    message = f"Ho creato il timesheet con ID: {timesheet.id} per il dipendente {employee.name} (ID: {employee.id}) relativo al viaggio {trip} (ID Viaggio: {id}) con orario di inizio {timesheet.datetime_start} e orario di fine {timesheet.datetime_stop}, per un totale di {ore:02d}:{minuti:02d} ore."
                    record.message_post(body=message, subtype_xmlid="mail.mt_note")
                    record.check = True

                else:
                    continue
                if indice == len(employees) and not contracts and not timesheet:
                    raise ValidationError(_(f"Il dipendente {employee.name} con id {employee.id} attualmente non ha alcun contratto valido. Contattare l'assistenza fornendo i dati appena forniti."))

            if driver['learning_driver_id']:
                for employee in employees_learning:
                    contracts = self.env['hr.contract'].search([
                        ('employee_id', '=', employee.id),
                        ('date_start', '<=', start_time),
                        '|', ('date_end', '>=', end_time), ('date_end', '=', False)
                    ])
                    if contracts:
                        _logger.info(contracts)
                        _logger.info(contracts[0].employee_id.id)
                        employee_id = contracts[0].employee_id.id
                        if learning_driver_id:
                            timesheet_learning = self.env['account.analytic.line'].sudo().create(
                            {
                                'date': start_time,
                                'project_id': project_id,
                                'task_id': task_id,
                                'employee_id': employee_id,
                                'datetime_start': start_datetime,
                                'datetime_stop': end_datetime,
                                'unit_amount': working_seconds,
                                'name': trip,
                                'gtms_id': id,
                            })
                            ore = int(working_seconds)
                            minuti = round((working_seconds - ore) * 60)
                            message = f"Ho creato il timesheet con ID: {timesheet_learning.id} per il dipendente {employee.name} (ID: {employee.id}) relativo al viaggio {trip} (ID Viaggio: {id}) con orario di inizio {timesheet_learning.datetime_start} e orario di fine {timesheet_learning.datetime_stop}, per un totale di {ore:02d}:{minuti:02d} ore."
                            record.message_post(body=message, subtype_xmlid="mail.mt_note")
                            record.check = True
                    _logger.info("FINITO")


    def test(self):
        _logger.info(self)
        # for record in self:
        #     _logger.info("CI PROVO")
        #     _logger.info(record.id)
        #     drivers = self.env['gtms.trip'].search_read([('id', '=', record.id)],['name','activity_calendar_event_id','drivers_payment', 'delivery_note_ids','drivers_ids'])
        #     _logger.info(drivers)


    # Funzione per il recupero del corretto hr.employee con contratto attivo nel momento del viaggio
    def get_active_employee_driver(self, driver, metodo_pagamento, start_time, end_time):
        _logger.info(f"Cerco il dipendente attivo per l'autista id {driver.id} nome {driver.name}, con metodo di pagamento {metodo_pagamento}, inizio {start_time}, fine {end_time}")
        employees = self.env['hr.employee'].search([('address_home_id', '=', driver.id), '|', ('active', '=', False),('active', '=', True)])
        # Per ogni dipendente associato al res.partner cerco il contratto attivo
        if not employees:
            raise ValidationError(_(f"L'autista {driver.name} non ha un dipendente associato. Contattare l'assistenza fornendo i dati appena forniti."))
        for employee in employees:
            contracts = self.env['hr.contract'].search([
                ('employee_id', '=', employee.id),
                ('date_start', '<=', start_time),
                '|', ('date_end', '>=', end_time), ('date_end', '=', False),
            ])
            if contracts:
                return contracts[0].employee_id.id


    # La funzione controlla se ci sono gli orari nel timesheet per ogni driver associato al viaggio, verificando metodo di pagamento
    def regenerate_hours_to_timesheet(self):
        for record in self:
            _logger.info(f"Rigenero le ore per il viaggio {record.name} con id {record.id} per i seguenti autisti: {record.all_drivers_ids.mapped('name')}")
            if record.state != 'checked':
                raise ValidationError(_(f"Il viaggio {record.name} deve essere sullo stato 'checked' per poter rigenerare le ore sul timesheet"))

            metodo_pagamento = record.drivers_payment
            if metodo_pagamento == "non_pagabile":
                raise ValidationError(_(f"Il viaggio {record.name} ha il metodo di pagamento 'Non pagabile', non è possibile rigenerare le ore sul timesheet"))
            elif metodo_pagamento == "ore_pianificate":
                ora_inizio = record.first_stop_planned_at
                ora_fine = record.last_stop_planned_at
            elif metodo_pagamento == "ore_effettive":
                ora_inizio = record.trip_start_from_survey
                ora_fine = record.trip_end_from_survey
            elif metodo_pagamento == "ore_macarena":
                ora_inizio = record.first_stop_planned_at
                ora_fine = record.trip_end_from_survey
            elif metodo_pagamento == "ore_macarena_inverso":
                ora_inizio = record.trip_start_from_survey
                ora_fine = record.last_stop_planned_at
            else:
                raise ValidationError(_(f"Il viaggio {record.name} non dispone di un metodo di pagamento per gli autisti, contattare l'assistenza fornendo i dati appena forniti."))
            _logger.info(f"Metodo di pagamento: {metodo_pagamento}, Ora inizio: {ora_inizio}, Ora fine: {ora_fine}")

            # Cerco gli orari inseriti nel Timesheet
            work_times = self.env['account.analytic.line'].sudo().search([('gtms_id', '=', record.id)])
            _logger.info(f"Orari trovati: {work_times}")

            # Controllo quali autisti hanno le ore sul timesheet
            drivers_with_hours = work_times.mapped('employee_id.address_home_id')
            # Trovo gli autisti che non hanno le ore sul timesheet
            missing_drivers = record.all_drivers_ids - drivers_with_hours
            _logger.info(f"Autisti con ore: {drivers_with_hours}, Autisti senza ore: {missing_drivers}")
            if missing_drivers:
                # Se mancano dei timesheet li rigenero
                for driver in missing_drivers:
                    _logger.info(f"Rigenero le ore per l'autista {driver.name}")
                    employee = record.get_active_employee_driver(driver, metodo_pagamento, ora_inizio, ora_fine)
                    employee_id = self.env['hr.employee'].sudo().browse(employee)
                    _logger.info(f"Ho trovato il dipendente con id {employee} per l'autista {driver.name}")
                    timesheet = self.env['account.analytic.line'].create({
                        'date': ora_inizio.date(),
                        'project_id': record.trip_type_id.task_id.project_id.id,
                        'task_id': record.trip_type_id.task_id.id,
                        'employee_id': employee,
                        'datetime_start': ora_inizio,
                        'datetime_stop': ora_fine,
                        # 'unit_amount': working_seconds,
                        'name': record.name,
                        'gtms_id': record.id,
                    })
                    _logger.info(f"Ho creato il timesheet con id {timesheet} per l'autista mancante")
                    work_time = ora_fine - ora_inizio
                    working_seconds = work_time.total_seconds() / 3600.0
                    ore = int(working_seconds)
                    minuti = round((working_seconds - ore) * 60)
                    message = f"Ho creato il timesheet con ID: {timesheet.id} per il dipendente {employee_id.name} (ID: {employee_id.id}) relativo al viaggio {record} (ID Viaggio: {record.id}) con orario di inizio {timesheet.datetime_start} e orario di fine {timesheet.datetime_stop}, per un totale di {ore:02d}:{minuti:02d} ore."
                    record.message_post(body=message, subtype_xmlid="mail.mt_note")