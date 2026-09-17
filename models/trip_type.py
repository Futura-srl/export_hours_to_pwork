import logging, datetime
from odoo import api, fields, models, http, _, Command
from odoo.exceptions import UserError, ValidationError
from datetime import datetime as dt


_logger = logging.getLogger(__name__)
now = datetime.datetime.now()


class Trip(models.Model):
    _inherit = "gtms.trip.type"

    task_id = fields.Many2one('project.task')
    causale_pwork = fields.Char()

    default_drivers_payment = fields.Selection([('ore_pianificate','Ore pianificate'),('ore_effettive','Ore effettive'),('ore_macarena','Ore Mix 1'),('ore_macarena_inverso','Ore Mix inverso'),('non_pagabile','Non pagare')], default='ore_effettive')
    # Solo i viaggi dei tipi con la spunta partecipano al caricamento ore su Pwork e alla chiusura del mese
    carica_ore_pwork = fields.Boolean(string="Carica ore su Pwork", default=False)
