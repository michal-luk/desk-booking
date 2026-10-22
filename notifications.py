"""E-mail notifications over SMTP (server set by an administrator: Settings → Mail).

Works with any mail server: Microsoft 365 (smtp.office365.com), Gmail / Google Workspace
(smtp.gmail.com), Postfix etc. Every message is written in the recipient's language. Sending
runs in a background thread - a booking does not wait for the mail server, and a mail error
goes to the audit log.
"""
import html
import json
import logging
import smtplib
import ssl
import threading
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import app_settings
import config
import db
import i18n
import theme
from i18n import _

log = logging.getLogger(__name__)


def configured(cfg=None):
    cfg = cfg or app_settings.get('mail')
    return bool(cfg.get('enabled') and cfg.get('host') and cfg.get('sender'))


def send(to, subject, body_html, cfg=None):
    """Synchronous - returns None or an error description."""
    cfg = cfg or app_settings.get('mail', with_secrets=True)
    if not (cfg.get('host') and cfg.get('sender')):
        return 'Mail is not configured.'
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = formataddr((cfg.get('sender_name') or '', cfg['sender']))
    msg['To'] = ', '.join(to)
    msg['Message-ID'] = make_msgid(domain=cfg['sender'].split('@')[-1])
    msg.set_content('HTML message - please open it in a mail program that displays HTML.')
    msg.add_alternative(body_html, subtype='html')
    port = int(cfg.get('port') or 587)
    context = ssl.create_default_context()
    try:
        if cfg.get('mode') == 'ssl':
            server = smtplib.SMTP_SSL(cfg['host'], port, timeout=30, context=context)
        else:
            server = smtplib.SMTP(cfg['host'], port, timeout=30)
        with server:
            if cfg.get('mode') == 'starttls':
                server.starttls(context=context)
            if cfg.get('login'):
                server.login(cfg['login'], cfg.get('password') or '')
            server.send_message(msg)
    except Exception as e:  # noqa: BLE001 - mail must not break the application
        return f'{type(e).__name__}: {e}'
    return None


def _in_background(to, subject, body_html):
    cfg = app_settings.get('mail', with_secrets=True)

    def work():
        error = send(to, subject, body_html, cfg)
        if error:
            log.warning('Message to %s was not sent: %s', to, error)
            try:
                conn = db.connect()   # thread outside the request - its own database connection
                conn.execute('INSERT INTO audit_log(at, actor, action, details) VALUES (?, ?, ?, ?)',
                             (db.now(), 'system', 'mail_error',
                              json.dumps({'to': to, 'subject': subject, 'error': error[:300]}, ensure_ascii=False)))
                conn.commit()
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    threading.Thread(target=work, daemon=True).start()


def _recipients(*emails):
    """Only people who have not switched notifications off; no duplicates."""
    emails = [e for e in dict.fromkeys(emails) if e]
    if not configured() or not emails:
        return []
    ph = ','.join('?' * len(emails))
    off = {r['email'] for r in db.query(
        f'SELECT email FROM users WHERE email IN ({ph}) AND (notifications = 0 OR active = 0)', emails)}
    return [e for e in emails if e not in off]


def _date(txt):
    d = date.fromisoformat(txt)
    return f'{i18n.weekday(d)} {d.strftime("%d.%m.%Y")}'


def _name(email):
    r = db.query('SELECT display_name FROM users WHERE email = ?', (email,), one=True)
    return (r['display_name'] if r else None) or email


def _layout(heading, body, button=None, footer=True):
    """Common e-mail frame. `button` = (label, path or full URL)."""
    label, path = button or (_('My bookings'), '/my')
    url = config.APP_URL + path if path.startswith('/') else path
    note = ('<p style="color:#6b7385;font-size:12px;margin-top:16px">'
            + html.escape(_('Automatic message. Notifications can be switched off in "My bookings".')) + '</p>') if footer else ''
    c = theme.colors()
    return f"""<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1d2433;max-width:560px">
      <div style="background:{c['header']};color:#fff;padding:12px 16px;border-radius:8px 8px 0 0;font-weight:600">
        <span style="color:{theme.mix(c['accent'], '#ffffff', 0.3)}">■</span> {html.escape(_('Desk booking'))}</div>
      <div style="border:1px solid #dbe3ef;border-top:0;padding:16px;border-radius:0 0 8px 8px">
        <h3 style="margin:0 0 10px">{html.escape(heading)}</h3>{body}
        <p style="margin-top:18px"><a href="{html.escape(url)}"
           style="background:{c['main']};color:#fff;padding:8px 14px;border-radius:6px;text-decoration:none">{html.escape(label)}</a></p>
        {note}
      </div></div>"""


def invitation(email, link, actor, purpose='invite'):
    """Link to set a password: an invitation of a new person or a password reset. Returns an error or None."""
    if not configured():
        return 'Mail is not configured.'
    with i18n.language(i18n.language_of(email)):
        if purpose == 'reset':
            subject, heading = _('Set a new password'), _('Password reset')
            body = (f'<p>{html.escape(_("We received a request to set a new password for the desk booking system."))}</p>'
                    f'<p>{html.escape(_("The link is valid for 24 hours. If it was not you, ignore this message."))}</p>')
        else:
            subject, heading = _('Invitation to the desk booking system'), _('You have an account in the desk booking system')
            body = (f'<p>{html.escape(_("{name} has created an account for you. Set a password to log in.", name=_name(actor)))}</p>'
                    f'<p>{html.escape(_("The link is valid for 72 hours."))}</p>')
        _in_background([email], subject, _layout(heading, body, (_('Set password'), link), footer=False))
    return None


def booking(actor, for_email, element, floor_name, results, time_from=None, time_to=None, title=None):
    """After a booking attempt: what succeeded, what did not and why - to the person and to whoever booked."""
    from booking import hours_text, place_name
    booked = [r['date'] for r in results if r['ok']]
    failed = [r for r in results if not r['ok']]
    for recipient in _recipients(for_email, actor):
        with i18n.language(i18n.language_of(recipient)):
            place, hours = place_name(element), hours_text(time_from, time_to)
            body = ''
            if actor != for_email:
                body += '<p>' + _('Booked by <b>{who}</b> for <b>{whom}</b>.', who=html.escape(_name(actor)),
                                  whom=html.escape(_name(for_email))) + '</p>'
            if title:
                body += '<p>' + _('Subject: <b>{title}</b>', title=html.escape(title)) + '</p>'
            if booked:
                body += ('<p>' + _('<b>{place}</b> · {floor} booked (<b>{hours}</b>) for:', place=html.escape(place),
                                   floor=html.escape(floor_name), hours=html.escape(hours)) + '</p><ul>'
                         + ''.join(f'<li>{_date(d)}</li>' for d in booked) + '</ul>')
            if failed:
                body += (f'<p style="color:#b91c1c">{html.escape(_("Could not be booked:"))}</p><ul>'
                         + ''.join(f'<li>{_date(r["date"])} - {html.escape(r["error"] or "")}</li>' for r in failed) + '</ul>')
            if booked and not failed:
                when = _('{n} days', n=len(booked)) if len(booked) > 1 else _date(booked[0])
                subject = _('Booked: {place} - {when}', place=place, when=when) + ('' if not time_from else f', {hours}')
                heading = _('Booking confirmed')
            elif booked:
                subject = _('{place}: booked {n} of {total} days', place=place, n=len(booked), total=len(results))
                heading = _('Partial booking')
            else:
                subject = _('Could not book: {place}', place=place)
                heading = _('Booking failed')
            _in_background([recipient], subject, _layout(heading, body))


def cancellation(actor, entries):
    """entries: [{'email', 'element', 'date', 'time_from', 'time_to'}]. Only when someone else cancelled."""
    from booking import hours_text, place_name
    by_person = {}
    for e in entries:
        if e['email'] != actor:
            by_person.setdefault(e['email'], []).append(e)
    for email, items in by_person.items():
        if not _recipients(email):
            continue
        with i18n.language(i18n.language_of(email)):
            body = ('<p>' + _('<b>{who}</b> cancelled your bookings:', who=html.escape(_name(actor))) + '</p><ul>'
                    + ''.join(f'<li>{_date(e["date"])} ({html.escape(hours_text(e["time_from"], e["time_to"]))}) - '
                              f'{html.escape(place_name(e["element"]))}</li>' for e in items) + '</ul>')
            _in_background([email], _('Booking cancelled'), _layout(_('Booking cancelled'), body))
