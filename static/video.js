/* Video fra dagen: upload i bidder.

   Cloudflare Tunnel afviser enhver request over 100 MB, og en telefonvideo er
   let større. Derfor sendes filen i bidder (størrelsen bestemmer serveren), og
   serveren sætter dem sammen. En bid, der fejler (dårlig dækning i skoven), sendes
   igen op til fem gange; serveren ignorerer en bid, den allerede har.

   Markup: <div data-video-upload="<start-url>"> med en <input type="file"> og en
   <ul class="videostatus"> til fremdriften. Når alle filer er oppe, genindlæses
   siden, så videoerne står i galleriet. */
(function () {
  var boks = document.querySelector('[data-video-upload]');
  if (!boks) return;
  var startUrl = boks.getAttribute('data-video-upload');
  var input = boks.querySelector('input[type=file]');
  var liste = boks.querySelector('.videostatus');

  function json(url, data) {
    return fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.fejl || ('Serveren svarede ' + r.status));
        return d;
      });
    });
  }

  function sendBid(url, fil, fra, bid, forsoeg) {
    return fetch(url + '?fra=' + fra, {
      method: 'PUT', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: fil.slice(fra, Math.min(fra + bid, fil.size))
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (r.ok) return d.modtaget;
        if (r.status === 409 && typeof d.modtaget === 'number') return d.modtaget;
        throw new Error(d.fejl || ('Serveren svarede ' + r.status));
      });
    }).catch(function (e) {
      if (forsoeg >= 5) throw e;
      return new Promise(function (ok) { setTimeout(ok, 2000 * (forsoeg + 1)); })
        .then(function () { return sendBid(url, fil, fra, bid, forsoeg + 1); });
    });
  }

  function upload(fil) {
    var li = document.createElement('li');
    var tekst = document.createElement('span');
    var bar = document.createElement('progress');
    bar.max = 100; bar.value = 0;
    tekst.textContent = fil.name + ' — starter …';
    li.appendChild(tekst); li.appendChild(bar);
    liste.appendChild(li);
    return json(startUrl, { navn: fil.name, stoerrelse: fil.size }).then(function (svar) {
      var url = startUrl.replace(/\/start$/, '/' + svar.id);
      function naeste(fra) {
        bar.value = Math.round(100 * fra / fil.size);
        tekst.textContent = fil.name + ' — ' + Math.round(fra / 1048576) + ' af ' +
          Math.round(fil.size / 1048576) + ' MB';
        if (fra >= fil.size) return json(url + '/faerdig', {});
        return sendBid(url, fil, fra, svar.bid, 0).then(naeste);
      }
      return naeste(0);
    }).then(function () {
      bar.value = 100;
      tekst.textContent = fil.name + ' — lagt på ✓';
      return true;
    }).catch(function (e) {
      li.classList.add('fejl');
      tekst.textContent = fil.name + ' — ' + e.message;
      return false;
    });
  }

  input.addEventListener('change', function () {
    var filer = Array.prototype.slice.call(input.files);
    if (!filer.length) return;
    input.disabled = true;
    // Én ad gangen: to store uploads side om side på en mobilforbindelse gør
    // bare begge langsommere og øger risikoen for, at begge fejler.
    var alleOk = true;
    filer.reduce(function (p, fil) {
      return p.then(function () { return upload(fil); })
              .then(function (ok) { alleOk = alleOk && ok; });
    }, Promise.resolve()).then(function () {
      input.disabled = false;
      input.value = '';
      if (alleOk) location.reload();
    });
  });

  // Advar, hvis man forlader siden midt i en upload.
  window.addEventListener('beforeunload', function (e) {
    if (input.disabled) { e.preventDefault(); e.returnValue = ''; }
  });
})();
