{
    'name': 'Export hours to Pwork',
    'version': '16',
    'author': "Luca Cocozza",
    'application': True,
    'description': "Con questo modulo è possibile esportare le ore dal Tms a Pwork",
    'depends': [
        'gtms',],
    'data': [
        # # # Settaggi per accesso ai contenuti
        # 'data/ir.model.access.csv',
        # # # Caricamento delle view,
        'view/trip_states.xml',
        # # Menu
        # 'view/menu.xml',
    ],
}
