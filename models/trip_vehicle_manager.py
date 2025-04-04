import logging, datetime
from odoo import api, fields, models, http, _, Command
from odoo.exceptions import UserError, ValidationError
from datetime import datetime as dt


_logger = logging.getLogger(__name__)
now = datetime.datetime.now()


class VehicleManager(models.Model):
    _inherit = "gtms.trip.vehicle.manager"


    # Nella funzione create controllo che il viaggio associato (trip_id) non sia con lo stato "checked"
    def create(self, vals):
        trip = self.env['gtms.trip'].browse(vals.get('trip_id'))
        if trip.state == 'checked':
            raise UserError(_("The trip is already checked."))
        return super(VehicleManager, self).create(vals)


    # Anche la funzione write deve controllare se il record e' associato ad un viaggio con stato checked. Nel caso deve mostrare l';errore. Il tutto deve esere con log

    def write(self, vals):
        _logger.info('Eseguo funzione write')  # Sostituisci con il nome della tua funzione
        trip = self.env['gtms.trip'].browse(self.trip_id)
        _logger.info(self.trip_id.state)
        _logger.info('ID viaggio: %s', trip.id)
        if self.trip_id.state == 'checked':
            raise UserError(_("The trip is already checked."))
        return super(VehicleManager, self).write(vals)