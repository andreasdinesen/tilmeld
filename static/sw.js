'use strict';
/* Tilmeld — service worker.
 *
 * Den har ÉN opgave: tage imod pushes og vise dem. Der er bevidst INGEN
 * offline-cache. Tilmeld er server-renderet, og en cache ville kunne servere
 * en gammel deltagerliste eller et event, der er aflyst — værre end ingenting.
 * Kommer der offline-understøttelse, skal den skrives med vilje, ikke opstå
 * som en bivirkning af push.
 *
 * Erfaringen fra doda (se RUNE-ERFARINGER.md): en push SKAL ende i en synlig
 * notifikation. Gør den ikke det, viser browseren sin egen »dette websted er
 * opdateret i baggrunden« — forvirrende og umulig at slippe af med. Derfor
 * slutter HVER gren nedenfor med showNotification, også fejlgrenen.
 */

const FALDBACK_IKON = '/static/icon-192.png';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('push', (e) => {
  e.waitUntil((async () => {
    /*
     * Browsere der forstår Declarative Web Push (`web_push: 8030`) når slet
     * ikke herind — dér viser SYSTEMET notifikationen uden at vække workeren.
     * Det er netop det led, seks forsøg strandede på i doda på iPhone.
     * Denne handler er vejen for alle de andre browsere.
     */
    let d = null;
    try { d = e.data ? e.data.json() : null; } catch (err) { d = null; }
    const n = (d && d.notification) || {};
    const titel = n.title || 'Tilmeld';
    const url = n.navigate || '/';

    try {
      await self.registration.showNotification(titel, {
        body: n.body || '',
        icon: n.icon || FALDBACK_IKON,
        badge: FALDBACK_IKON,
        // Samme tag betyder at en ny besked ERSTATTER den forrige i stedet for
        // at stable sig op. Pr. destination, så et event ikke skubber et andet væk.
        tag: 'tilmeld:' + url,
        data: { url: url },
      });
    } catch (err) {
      // Kunne der ikke vises noget, er DET svaret — en helt anden fejl end en
      // push, der aldrig kom frem.
      await self.registration.showNotification('Tilmeld', {
        body: 'Der er nyt om et event.',
        icon: FALDBACK_IKON,
        data: { url: '/' },
      });
    }
  })());
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const maal = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil((async () => {
    // Er siden allerede åben, skal den frem — ikke åbnes én gang til.
    const vinduer = await clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const v of vinduer) {
      if (v.url === maal && 'focus' in v) return v.focus();
    }
    return clients.openWindow(maal);
  })());
});
