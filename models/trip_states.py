from odoo import fields, models, api
import logging, datetime


_logger = logging.getLogger(__name__)
now = datetime.datetime.now()


class FleetFuelDriver(models.Model):
    _inherit = "gtms.trip.states"


    
    
