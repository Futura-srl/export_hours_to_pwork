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