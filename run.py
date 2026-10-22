"""Start the application: python run.py

Without SSL it is served by waitress (a production WSGI server) - HTTPS is then provided by
a reverse proxy (Apache, nginx, IIS). With SSL_CERT/SSL_KEY in .env the app encrypts the traffic itself.
"""
import logging

import config
from app import create_app

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s %(name)s: %(message)s')
app = create_app()

if __name__ == '__main__':
    print(f'Desk booking: {config.APP_URL}/  (ENV={config.ENV})')
    if config.SSL_CERT and config.SSL_KEY:
        app.run(host=config.APP_HOST, port=config.APP_PORT, debug=config.APP_DEBUG,
                ssl_context=(config.SSL_CERT, config.SSL_KEY))
    elif config.APP_DEBUG:
        app.run(host=config.APP_HOST, port=config.APP_PORT, debug=True)
    else:
        from waitress import serve
        serve(app, host=config.APP_HOST, port=config.APP_PORT, threads=8)
