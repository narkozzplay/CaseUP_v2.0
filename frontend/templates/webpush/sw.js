/* CaseUP service worker: shows web pushes and opens the right page on click. */
'use strict';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch (error) {
        data = { body: event.data ? event.data.text() : '' };
    }
    const options = {
        body: data.body || '',
        icon: data.icon || undefined,
        badge: data.badge || undefined,
        data: { url: data.url || '/' },
        lang: 'ru',
    };
    if (data.tag) {
        options.tag = data.tag;
        options.renotify = true;
    }
    event.waitUntil(self.registration.showNotification(data.title || 'CaseUP', options));
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const target = new URL((event.notification.data && event.notification.data.url) || '/', self.location.origin).href;
    event.waitUntil((async () => {
        const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
        const exact = windows.find((client) => client.url === target);
        if (exact) {
            return exact.focus();
        }
        const ours = windows.find((client) => client.url.startsWith(self.location.origin) && 'navigate' in client);
        if (ours) {
            const client = await ours.navigate(target);
            return client ? client.focus() : self.clients.openWindow(target);
        }
        return self.clients.openWindow(target);
    })());
});
