from datetime import timedelta

from odoo import fields, models


class PworkCaricaOreWizard(models.TransientModel):
    _name = "pwork.carica.ore.wizard"
    _description = "Carica ore su Pwork"

    data_a = fields.Date(string="Carica fino al", required=True,
                         default=lambda self: self.env['pwork.caricamento']._oggi() - timedelta(days=1),
                         help="I giorni si caricano in ordine dal primo giorno dopo l'ultimo mese chiuso, al massimo fino a ieri.")
    risultato = fields.Text(string="Risultato", readonly=True)

    def action_carica(self):
        self.ensure_one()
        testo = self.env['pwork.caricamento']._esegui_manuale(self.data_a)
        self.risultato = testo or "Nessun giorno da caricare."
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
