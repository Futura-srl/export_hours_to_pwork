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
    trip_start_from_survey = fields.Datetime(states={'checked': [('readonly', False)]})
    trip_end_from_survey = fields.Datetime(states={'checked': [('readonly', False)]})
    drivers_payment = fields.Selection([('ore_pianificate','Ore pianificate'),('ore_effettive','Ore effettive'),('ore_macarena','Ore Mix 1'),('ore_macarena_inverso','Ore Mix inverso'),('non_pagabile','Non pagare')], default='ore_pianificate')
    all_drivers_ids = fields.One2many('res.partner', compute="_find_all_drivers_ids")

    state = fields.Selection(_states_list,
                             string='Status', readonly=True, copy=False, index=True,
                             default='draft', compute='_compute_state', store=True, tracking=True)


    def _find_all_drivers_ids(self):
        for field in self:
            drivers = []
            for trip in self:
                trip.all_drivers_ids = False
            # Cerco tutti i record della tabella gtms.trip.vehicle.manager associati al viaggio
            data = self.env['gtms.trip.vehicle.manager'].search_read([('trip_id', '=', self.id)],['driver_id','learning_driver_id'])
            for record in data:
                if record['driver_id'] != False:
                    driver_1 = record['driver_id'][0]
                    drivers.append(driver_1)
                if record['learning_driver_id'] != False:
                    driver_2 = record['learning_driver_id'][0]
                    drivers.append(driver_2)
            field.all_drivers_ids = list(set(drivers))


    
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
    def _compute_state(self):
        # Chiamata al metodo originale per mantenere le funzionalità esistenti
        super(Trip, self)._compute_state()

        # Aggiungi qui le tue operazioni aggiuntive
        for trip in self:
            # Esempio: Se il tuo campo personalizzato è True, imposta lo stato su 'personalizzato'
            if trip.check == True:
                trip.state = 'checked'
                trip.is_readonly = True

    
    def unchecked(self):
        for record in self:
            # Cerco gli orari inseriti nel Timesheet prima di rimuoverli
            work_times = self.env['account.analytic.line'].search([('gtms_id', '=', record.id)])
            
            # Controllo se ci sono orari già convalidati
            if any(work_time.validated_status in ['validated','processed','done'] for work_time in work_times):
                raise ValidationError(_("Il viaggio contiene degli orari già convalidati su Pwork"))

            # Rimuovo gli orari dal timesheet
            work_times.unlink()
            record.check = False
    
    
    def checked(self):
        for record in self:
            if record.state == 'checked':
                continue
            id = record.id
            trip = record.name
            trip_type_id = record.trip_type_id.id
            task_id = self.env['gtms.trip.type'].search_read([('id', '=', trip_type_id)], ['task_id'])[0]['task_id'][0]
            project_id = self.env['project.task'].search_read([('id', '=', task_id)], ['project_id'])[0]['project_id'][0]
            
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
                self.check = True
                continue

            work_time = end_datetime - start_datetime
            working_seconds = work_time.total_seconds() / 3600.0
            
            # Cerco gli autisti che hanno guidato durante il viaggio
            drivers = self.env['gtms.trip.vehicle.manager'].search_read([('trip_id', '=', id)],['driver_id', 'learning_driver_id'])
            # drivers = list({tuple(driver.items()) for driver in drivers})
            for driver in drivers:
                driver_id = driver['driver_id'][0]
                if driver['learning_driver_id']:
                    learning_driver_id = driver['learning_driver_id'][0]
                _logger.info(driver)
                _logger.info(driver['driver_id'])
                _logger.info(driver['learning_driver_id'])
            
            
            # Cerco il dipendente con contratto attivo al momento della partenza del viaggio
            employees = self.env['hr.employee'].search([('address_home_id', '=', driver_id), '|', ('active', '=', False),('active', '=', True)])
            if driver['learning_driver_id']:
                employees_learning = self.env['hr.employee'].search([('address_home_id', '=', learning_driver_id), '|', ('active', '=', False),('active', '=', True)])
                _logger.info(employees_learning)
            _logger.info(employees)
            # Utilizzo indice per essere certo di aver controllato tutti i dipendenti associati al res.partner e nel caso non ci fossero contratti attivi eseguo l'errore
            _logger.info("Setto indice = 1")
            indice = 1
            for employee in employees:
                _logger.info(f"Indice = {indice}, len = {len(employees)}")
                contracts = self.env['hr.contract'].search([
                    ('employee_id', '=', employee.id),
                    ('date_start', '<=', start_time),
                    '|', ('date_end', '>=', end_time), ('date_end', '=', False),
                ])
                _logger.info("XXXXXXXXXXXXX")
                _logger.info(contracts)
                timesheet = False
                if contracts:
                    _logger.info(contracts[0].employee_id.id)
                    employee_id = contracts[0].employee_id.id
                    

                    # Creo il Timesheet
                    timesheet = self.env['account.analytic.line'].create(
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
                    self.check = True
                    indice = 1 + indice
                else:
                    indice = 1 + indice
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
                            timesheet_learning = self.env['account.analytic.line'].create(
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
                            self.check = True
                    _logger.info("FINITO")


    def test(self):
        _logger.info(self)
        # for record in self:
        #     _logger.info("CI PROVO")
        #     _logger.info(record.id)
        #     drivers = self.env['gtms.trip'].search_read([('id', '=', record.id)],['name','activity_calendar_event_id','drivers_payment', 'delivery_note_ids','drivers_ids'])
        #     _logger.info(drivers)


