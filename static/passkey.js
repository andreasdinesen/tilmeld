// Passkeys (WebAuthn) i browseren. Serveren laver alt det tunge — her oversættes
// bare mellem WebAuthns ArrayBuffers og det base64url, JSON kan bære.
//
// WebAuthn findes KUN i et sikkert kontekst (https eller localhost). Over almindelig
// http — fx yggdrasil-panelets IP:port — er window.PublicKeyCredential undefined.
// Derfor skjules knapperne, i stedet for at fejle når man trykker på dem.

(function () {
  var b64 = {
    dec: function (s) {
      s = s.replace(/-/g, '+').replace(/_/g, '/');
      var bin = atob(s + '==='.slice((s.length + 3) % 4));
      var out = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
      return out.buffer;
    },
    enc: function (buf) {
      var bytes = new Uint8Array(buf), s = '';
      for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
      return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }
  };

  function supported() {
    return !!(window.PublicKeyCredential && navigator.credentials && window.isSecureContext);
  }

  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) throw new Error(data.error || ('Serverfejl ' + r.status));
        return data;
      });
    });
  }

  // Serverens options er ren JSON; buffer-felterne skal pakkes ud før browseren vil se på dem.
  function prepCreate(o) {
    o.challenge = b64.dec(o.challenge);
    o.user.id = b64.dec(o.user.id);
    (o.excludeCredentials || []).forEach(function (c) { c.id = b64.dec(c.id); });
    return o;
  }
  function prepGet(o) {
    o.challenge = b64.dec(o.challenge);
    (o.allowCredentials || []).forEach(function (c) { c.id = b64.dec(c.id); });
    return o;
  }

  function packCreate(cred) {
    return {
      id: cred.id, type: cred.type, rawId: b64.enc(cred.rawId),
      response: {
        clientDataJSON: b64.enc(cred.response.clientDataJSON),
        attestationObject: b64.enc(cred.response.attestationObject)
      },
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {}
    };
  }
  function packGet(cred) {
    return {
      id: cred.id, type: cred.type, rawId: b64.enc(cred.rawId),
      response: {
        clientDataJSON: b64.enc(cred.response.clientDataJSON),
        authenticatorData: b64.enc(cred.response.authenticatorData),
        signature: b64.enc(cred.response.signature),
        userHandle: cred.response.userHandle ? b64.enc(cred.response.userHandle) : null
      },
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {}
    };
  }

  // Brugeren kan afbryde systemets passkey-dialog — det er ikke en fejl at vise frem.
  function aborted(e) {
    return e && (e.name === 'NotAllowedError' || e.name === 'AbortError');
  }

  window.Passkey = {
    supported: supported,

    // Vis kun elementer med [data-passkey] hvis browseren rent faktisk kan det.
    reveal: function () {
      if (!supported()) return false;
      document.querySelectorAll('[data-passkey]').forEach(function (el) { el.hidden = false; });
      return true;
    },

    register: function (scope, slug, name, statusEl) {
      var say = function (t, cls) { if (statusEl) { statusEl.textContent = t; statusEl.className = 'formnote ' + (cls || ''); } };
      say('Følg din browsers vejledning …');
      return post('/webauthn/register/options', { scope: scope, slug: slug })
        .then(function (o) { return navigator.credentials.create({ publicKey: prepCreate(o) }); })
        .then(function (cred) {
          return post('/webauthn/register/verify',
                      { scope: scope, slug: slug, name: name, credential: packCreate(cred) });
        })
        .then(function () { location.reload(); })
        .catch(function (e) {
          say(aborted(e) ? 'Afbrudt.' : ('Kunne ikke tilføje passkey: ' + e.message), 'bad');
        });
    },

    login: function (scope, slug, statusEl) {
      var say = function (t, cls) { if (statusEl) { statusEl.textContent = t; statusEl.className = 'formnote ' + (cls || ''); } };
      say('Følg din browsers vejledning …');
      return post('/webauthn/login/options', { scope: scope, slug: slug })
        .then(function (o) { return navigator.credentials.get({ publicKey: prepGet(o) }); })
        .then(function (cred) {
          return post('/webauthn/login/verify',
                      { scope: scope, slug: slug, credential: packGet(cred) });
        })
        .then(function (r) { location.href = r.redirect || '/'; })
        .catch(function (e) {
          say(aborted(e) ? 'Afbrudt.' : ('Login med passkey mislykkedes: ' + e.message), 'bad');
        });
    },

    remove: function (scope, slug, id, statusEl) {
      return post('/webauthn/delete', { scope: scope, slug: slug, id: id })
        .then(function () { location.reload(); })
        .catch(function (e) {
          if (statusEl) { statusEl.textContent = 'Kunne ikke slette: ' + e.message; statusEl.className = 'formnote bad'; }
        });
    }
  };
})();
