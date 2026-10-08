// Aide Node pour la plateforme GPS (deepstream). Lit une requête JSON sur stdin, écrit la réponse JSON sur stdout.
// {url, path, username, password, commande, ...}
//   commande "vehicules"  → [{CVEH, LVEH, CBOX}]
//   commande "positions"  → {CBOX: {lat, lng, vitesse, heure (fixTime), moteur, mouvement, en_ligne}} pour cbox: [..]
//   commande "journal"    → {journal: {CBOX: [lignes]}, positions: {}} pour cbox: [..], dt1, dt2 (ISO UTC), stoplen (s)
//   commande "suivi"      → journal + positions live, en une seule connexion
// Reconstruire le bundle après modification : dans un dossier de travail (hors dépôt),
//   npm install @deepstream/client@7   puis
//   NODE_PATH=<ce dossier>/node_modules <frappe-bench>/apps/frappe/node_modules/.bin/esbuild <chemin>/flotte_gps_client.src.js \
//     --bundle --platform=node --target=node18 --minify --outfile=<chemin>/flotte_gps_client.bundle.js
// (esbuild résout @deepstream/client par NODE_PATH ; rien à installer en prod, Node y est déjà.)
const { DeepstreamClient } = require('@deepstream/client');

function lire() {
  return new Promise((resolve, reject) => {
    let s = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (c) => (s += c));
    process.stdin.on('end', () => { try { resolve(JSON.parse(s)); } catch (e) { reject(e); } });
  });
}

async function main() {
  const q = await lire();
  const client = new DeepstreamClient(q.url || 'wss://www.unidevpro.com.tn/api',
    { path: q.path || '/api', rpcResponseTimeout: q.timeout || 60000, maxReconnectAttempts: 1 });
  const erreurs = [];
  client.on('error', (e, ev) => erreurs.push(String(ev || e)));
  const garde = setTimeout(() => { process.stdout.write(JSON.stringify({ erreur: 'delai depasse', erreurs })); process.exit(2); }, (q.timeout || 60000) + 15000);
  const auth = await client.login({ username: q.username, password: q.password, role: 'user' });
  const token = auth && auth.access_token;
  if (!token) throw new Error('login refuse');
  const rpc = (nom, params) => client.rpc.make(nom, Object.assign({ access_token: token }, params || {}));
  let out;
  if (q.commande === 'vehicules') {
    const vehs = await rpc('getVehs', {});
    out = vehs.map((v) => ({ CVEH: v.CVEH, LVEH: (v.LVEH || '').trim(), CBOX: String(v.CBOX), BOXSTATE: v.BOXSTATE, IS_VEH_ACTIF: v.IS_VEH_ACTIF }));
  } else if (q.commande === 'positions') {
    out = {};
    for (const cbox of q.cbox || []) {
      const rec = client.record.getRecord('tracker/' + cbox);
      try {
        await rec.whenReady();
        const d = rec.get() || {};
        const p = (d.tracker && d.tracker.position) || {};
        const a = p.attributes || {};
        out[String(cbox)] = { lat: p.latitude, lng: p.longitude, vitesse: p.speed, heure: p.fixTime || p.deviceTime,
          moteur: !!a.ignition, mouvement: !!a.motion, en_ligne: !!(d.connection && d.connection.status === 'online'),
          derniere_connexion: d.connection && d.connection.lastUpdate, cap: p.course };
      } catch (e) { out[String(cbox)] = { erreur: String(e) }; }
      rec.discard();
    }
  } else if (q.commande === 'journal' || q.commande === 'suivi') {
    out = { journal: {}, positions: {} };
    for (const cbox of q.cbox || []) {
      try {
        out.journal[String(cbox)] = await rpc('journal', { CBOX: cbox, DT1: q.dt1, DT2: q.dt2, STOPLEN: q.stoplen || 60 });
      } catch (e) { out.journal[String(cbox)] = { erreur: String(e) }; }
      if (q.commande === 'suivi') {
        const rec = client.record.getRecord('tracker/' + cbox);
        try {
          await rec.whenReady();
          const d = rec.get() || {};
          const p = (d.tracker && d.tracker.position) || {};
          const a = p.attributes || {};
          out.positions[String(cbox)] = { lat: p.latitude, lng: p.longitude, vitesse: p.speed, heure: p.fixTime || p.deviceTime,
            moteur: !!a.ignition, mouvement: !!a.motion, en_ligne: !!(d.connection && d.connection.status === 'online'),
            derniere_connexion: d.connection && d.connection.lastUpdate, cap: p.course };
        } catch (e) { out.positions[String(cbox)] = { erreur: String(e) }; }
        rec.discard();
      }
    }
  } else {
    throw new Error('commande inconnue : ' + q.commande);
  }
  clearTimeout(garde);
  process.stdout.write(JSON.stringify({ resultat: out, erreurs }));
  client.close();
  setTimeout(() => process.exit(0), 200);
}

main().catch((e) => { process.stdout.write(JSON.stringify({ erreur: String(e && e.message || e) })); process.exit(1); });
