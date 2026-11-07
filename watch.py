#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Внешний наблюдатель за вики: проверка с серверов GitHub, письмо при падении и при восстановлении.

Адреса и пароль ящика приходят только из секретов (WATCH_URL, SMTP_USER, SMTP_PASS, MAIL_TO), в журнал не печатаются.
Состояние хранится в открытом issue с меткой: есть открытый issue = вики считается лежащей, письмо уже ушло.
"""
import json
import os
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.request
from email.message import EmailMessage

TRIES, PAUSE = 3, 60          # три попытки с паузой минута: две подряд неудачные проверки не считаются случайностью
BACKUP_MAX_HOURS = 26         # тот же порог, что у прежнего сторожа на сервере 6Б
LABELS = {'down': 'вики-не-отвечает', 'stale': 'копия-устарела'}


def check(url):
    """Возвращает (состояние, описание): ok, down или stale."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'wiki-watch'}), timeout=20) as r:
            body = json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        return 'down', 'ответ %s' % e.code
    except Exception as e:  # сеть, таймаут, битый ответ: для наблюдателя все это одно и то же "не отвечает"
        return 'down', type(e).__name__
    if not body.get('ok'):
        return 'down', 'проверка вики вернула ok=false'
    age = body.get('backupAgeHours')
    if age is None or age > BACKUP_MAX_HOURS:
        return 'stale', 'копии базы %s ч' % age
    if body.get('restoreOk') is False:
        return 'stale', 'проверка восстановления из копии красная'
    return 'ok', 'копия %.1f ч назад' % age


def probe(url):
    state, info = check(url)
    for _ in range(TRIES - 1):
        if state == 'ok':
            break
        time.sleep(PAUSE)
        state, info = check(url)
    return state, info


def gh(method, path, body=None):
    req = urllib.request.Request('https://api.github.com/repos/%s%s' % (os.environ['GITHUB_REPOSITORY'], path), method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
                                          'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b'null')


def open_issue(kind):
    items = gh('GET', '/issues?state=open&labels=%s' % urllib.request.quote(LABELS[kind]))
    return items[0] if items else None


def mail(subject, text):
    msg = EmailMessage()
    msg['From'] = os.environ['SMTP_USER']
    msg['To'] = os.environ['MAIL_TO']
    msg['Subject'] = subject
    msg.set_content(text)
    with smtplib.SMTP_SSL('smtp.yandex.ru', 465, context=ssl.create_default_context(), timeout=30) as s:
        s.login(os.environ['SMTP_USER'], os.environ['SMTP_PASS'])
        s.send_message(msg)


def now():
    return time.strftime('%d.%m.%Y %H:%M', time.gmtime(time.time() + 3 * 3600)) + ' МСК'


def main():
    url = os.environ['WATCH_URL']
    if os.environ.get('DRILL') == 'true':
        # учение: проверяем заведомо несуществующий адрес той же вики, состояние не трогаем
        state, info = probe(url.rsplit('/', 1)[0] + '/nonexistent-drill')
        mail('[УЧЕНИЕ] Вики не отвечает', 'Это учебная тревога, вики работает.\nВремя: %s\nПричина в учении: %s\n' % (now(), info))
        print('учение: письмо отправлено, состояние проверки', state)
        return
    state, info = probe(url)
    print('состояние:', state, info)
    for kind in ('down', 'stale'):
        issue = open_issue(kind)
        bad = state == kind
        if bad and not issue:
            title = 'Вики не отвечает' if kind == 'down' else 'Копия базы вики устарела'
            mail(title, '%s.\nВремя: %s\nЧто видно снаружи: %s\n\nПроверка с серверов GitHub каждые 10 минут. '
                        'Когда все вернется, придет письмо "снова в порядке".\n' % (title, now(), info))
            gh('POST', '/issues', {'title': title + ' с ' + now(), 'labels': [LABELS[kind]], 'body': info})
            print('тревога отправлена:', kind)
        elif not bad and issue and state == 'ok':
            title = 'Вики снова в порядке' if kind == 'down' else 'Копия базы вики снова свежая'
            mail(title, '%s.\nВремя: %s\nПроблема началась: %s\nСейчас: %s\n' % (title, now(), issue['title'], info))
            gh('PATCH', '/issues/%d' % issue['number'], {'state': 'closed'})
            print('отбой отправлен:', kind)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('сбой наблюдателя:', type(e).__name__, file=sys.stderr)
        raise
