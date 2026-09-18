'use strict';
/* Tilmeld — slå notifikationer til på DENNE enhed.
 *
 * Bruges af makroen i templates/_push.html. Hvert element med
 * [data-push] bliver til et lille panel med én knap.
 *
 * Den vigtigste erfaring fra doda: sig HVILKEN forudsætning der mangler.
 * En knap, der bare ikke virker, er det værste svar — især på iPhone, hvor
 * PushManager kun findes i en app, der ligger på hjemmeskærmen.
 */
(function () {
  function b64uTilBytes(s) {
    const b64 = s.replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(b64 + '='.repeat((4 - (b64.length % 4)) % 4));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  /** Hvorfor kan der ikke sendes til denne enhed? "" = alt er i orden. */
  function hvorfor_ikke() {
    if (!window.isSecureContext) {
      return 'Notifikationer kræver https. Over almindelig http har browseren dem slet ikke.';
    }
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      // iOS har KUN PushManager i en app, der er lagt på hjemmeskærmen.
      if (/iPad|iPhone|iPod/.test(navigator.userAgent)) {
        return 'På iPhone og iPad virker det kun, når siden ligger på hjemmeskærmen: '
             + 'tryk Del → »Føj til hjemmeskærm«, og åbn den derfra.';
      }
      return 'Denne browser understøtter ikke notifikationer.';
    }
    if (Notification.permission === 'denied') {
      return 'Notifikationer er blokeret for dette websted i browserens indstillinger.';
    }
    return '';
  }

  async function json(url, krop) {
    const r = await fetch(url, {
      method: krop ? 'POST' : 'GET',
      credentials: 'same-origin',
      headers: krop ? { 'Content-Type': 'application/json' } : {},
      body: krop ? JSON.stringify(krop) : undefined,
    });
    const d = await r.json().catch(function () { return {}; });
    if (!r.ok) throw new Error(d.error || ('fejl ' + r.status));
    return d;
  }

  function opsaet(box) {
    const slug = box.dataset.slug;
    const scope = box.dataset.push;
    const knap = box.querySelector('[data-push-btn]');
    const test = box.querySelector('[data-push-test]');
    const note = box.querySelector('[data-push-note]');
    let reg = null;

    function sig(tekst, daarlig) {
      note.textContent = tekst;
      note.classList.toggle('bad', !!daarlig);
    }

    async function abonnement() {
      if (!reg) reg = await navigator.serviceWorker.ready;
      return reg.pushManager.getSubscription();
    }

    async function tegn() {
      const a = await abonnement();
      knap.textContent = a ? 'Slå notifikationer fra' : 'Slå notifikationer til';
      knap.classList.toggle('danger', !!a);
      test.hidden = !a;
      if (!note.textContent) {
        sig(a ? 'Denne enhed får besked.' : 'Denne enhed får ingen besked endnu.');
      }
    }

    async function slaaTil() {
      // requestPermission SKAL komme fra et klik — derfor ligger den her og
      // ikke i en opstartsrutine.
      const svar = await Notification.requestPermission();
      if (svar !== 'granted') throw new Error('Du sagde nej til notifikationer.');
      const d = await json('/' + slug + '/push/key');
      const a = await (await navigator.serviceWorker.ready).pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: b64uTilBytes(d.publicKey),
      });
      const j = a.toJSON();
      await json('/' + slug + '/push/subscribe', {
        scope: scope,
        endpoint: j.endpoint,
        p256dh: j.keys && j.keys.p256dh,
        auth: j.keys && j.keys.auth,
        label: navigator.platform || '',
      });
    }

    async function slaaFra() {
      const a = await abonnement();
      if (!a) return;   // intet at melde fra på DENNE enhed
      await json('/' + slug + '/push/unsubscribe', { endpoint: a.endpoint });
      await a.unsubscribe();
    }

    knap.addEventListener('click', async function () {
      knap.disabled = true;
      sig('Et øjeblik …');
      try {
        const a = await abonnement();
        if (a) { await slaaFra(); sig('Notifikationer slået fra på denne enhed.'); }
        else { await slaaTil(); sig('Klar — denne enhed får nu besked.'); }
      } catch (e) {
        sig(e.message || 'Det gik ikke.', true);
      }
      knap.disabled = false;
      const gemt = note.textContent;
      await tegn();
      sig(gemt, note.classList.contains('bad'));
    });

    test.addEventListener('click', async function () {
      test.disabled = true;
      sig('Sender en prøve …');
      try {
        const a = await abonnement();
        const d = await json('/' + slug + '/push/test',
          { scope: scope, endpoint: a ? a.endpoint : '' });
        sig(d.sent ? 'Prøven er sendt — den skulle komme om et øjeblik.'
                   : 'Ingen enheder at sende til.', !d.sent);
      } catch (e) {
        sig(e.message || 'Prøven kunne ikke sendes.', true);
      }
      test.disabled = false;
    });

    const grund = hvorfor_ikke();
    if (grund) {
      knap.hidden = true;
      test.hidden = true;
      sig(grund, true);
      return;
    }
    // Service workeren ligger i roden, så den kan styre hele webstedet.
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .then(function (r) { reg = r; return navigator.serviceWorker.ready; })
      .then(tegn)
      .catch(function (e) { sig('Kunne ikke starte service worker: ' + e.message, true); });
  }

  document.querySelectorAll('[data-push]').forEach(opsaet);
})();
