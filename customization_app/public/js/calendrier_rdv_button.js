/**
 * Calendrier workspace — "📅 Nouveau RDV" floating button
 *
 * Visible only for the Economiq Solutions partner user.
 * Step 1 : pick any customer (bypasses sharing via custom search API)
 * Step 2 : open the same full-screen calendar overlay used in Liste d'Appelle
 * Step 3 : after slot selection → open RDV details dialog → save Tache de travail
 */

(function () {

    const ALLOWED_USER     = 'economiqaquasolutions23@gmail.com';
    const TARGET_WORKSPACE = 'Calendrier';
    const BTN_ID           = 'rdv-libre-fab';

    // Couleur décidée côté serveur (compute_tache_color) — plus de mapping ici.
    const INTERVENTION_ICONS = {
        'Entretien': '🔧 ', 'Installation': '🔨 ', 'Réparation': '🧰 ',
        'Livraison': '🚐 ', 'Visite': '🚗 ', 'Autre': '☕ ',
    };
    const INTERVENTION_DURATIONS = {
        'Entretien': 45, 'Installation': 105,
        'Réparation': 120, 'Livraison': 30,
        'Visite': 120, 'Autre': 120,
    };

    /* ── Inject floating button ───────────────────────────────────────────── */
    function inject_button() {
        if (frappe.session.user !== ALLOWED_USER) return;
        if (document.getElementById(BTN_ID)) return;
        const btn = document.createElement('button');
        btn.id = BTN_ID;
        btn.innerHTML = '📅 Nouveau RDV';
        btn.title = "Prendre un rendez-vous pour n'importe quel client";
        Object.assign(btn.style, {
            position: 'fixed', bottom: '28px', right: '28px', zIndex: '9999',
            padding: '10px 18px', background: '#2490ef', color: '#fff',
            border: 'none', borderRadius: '24px', fontSize: '13px',
            fontWeight: '600', cursor: 'pointer', boxShadow: '0 4px 12px rgba(0,0,0,0.25)',
        });
        btn.onmouseenter = () => btn.style.background = '#1a73d4';
        btn.onmouseleave = () => btn.style.background = '#2490ef';
        btn.onclick = openOverlay;
        document.body.appendChild(btn);
        setTimeout(inject_help_tip, 300);
    }

    function inject_help_tip() {
        var HELP_KEY = 'help_rdv_btn';
        if (localStorage.getItem(HELP_KEY) === 'hidden') return;
        if (document.getElementById('rdv-help-tip')) return;
        if (!document.getElementById(BTN_ID)) return;
        var collapsed = localStorage.getItem(HELP_KEY + '_c') === '1';
        var $tip = $("<div id='rdv-help-tip' style='"
            + "position:fixed;bottom:80px;right:20px;z-index:9998;"
            + "width:310px;background:linear-gradient(135deg,#e8f5fd,#f0f9ff);"
            + "border:1px solid #90caf9;border-radius:10px;padding:10px 14px;"
            + "box-shadow:0 4px 14px rgba(0,100,200,0.15);font-family:inherit;'>"
            + "<div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;'>"
            + "<strong style='font-size:12px;color:#1565c0;'>💡 Bouton Nouveau RDV</strong>"
            + "<div style='display:flex;gap:10px;'>"
            + "<button class='_rht' style='font-size:11px;color:#1565c0;background:none;border:none;cursor:pointer;padding:0;'>"
            + (collapsed ? '▶ Afficher' : '▼ Réduire') + "</button>"
            + "<button class='_rhc' style='font-size:11px;color:#aaa;background:none;border:none;cursor:pointer;padding:0;'>✕</button>"
            + "</div></div>"
            + "<div class='_rhb' style='display:" + (collapsed ? 'none' : 'block') + ";'>"
            + "<div style='font-size:12px;color:#333;line-height:1.6;'>"
            + "Ce bouton vous permet de prendre un rendez-vous pour <strong>n'importe quel client</strong> de la base de données — même ceux auxquels vous n'avez pas normalement accès."
            + "</div>"
            + "<div style='margin-top:8px;font-size:11px;color:#555;line-height:1.5;'>"
            + "� Cliquez sur un créneau libre dans le calendrier pour sélectionner l'heure, puis remplissez les détails du rendez-vous dans le formulaire qui s'ouvre."
            + "</div></div></div>");
        $tip.find('._rht').on('click', function() {
            var $b = $tip.find('._rhb'); var v = $b.is(':visible');
            $b.toggle(); $(this).text(v ? '▶ Afficher' : '▼ Réduire');
            localStorage.setItem(HELP_KEY + '_c', v ? '1' : '0');
        });
        $tip.find('._rhc').on('click', function() {
            localStorage.setItem(HELP_KEY, 'hidden');
            $tip.fadeOut(200, function() { $tip.remove(); });
        });
        $('body').append($tip);
    }

    function remove_button() {
        const b = document.getElementById(BTN_ID);
        if (b) b.remove();
        $('#rdv-help-tip').remove();
    }

    /* ── Watch route changes ──────────────────────────────────────────────── */
    function check_route() {
        if (frappe.session.user !== ALLOWED_USER) return;
        const hash  = decodeURIComponent((window.location.hash || '').replace(/^#\/?/, ''));
        const parts = hash.split('/');
        const onCal = (parts[0] === 'Workspaces' && parts[1] === TARGET_WORKSPACE)
                   || parts[0] === TARGET_WORKSPACE
                   || (frappe.get_route && frappe.get_route() && frappe.get_route()[1] === TARGET_WORKSPACE);
        if (onCal) { inject_button(); inject_help_tip(); } else { remove_button(); }
    }

    $(document).on('page-change', check_route);
    $(window).on('hashchange', check_route);
    frappe.ready
        ? frappe.ready(function () { setTimeout(check_route, 800); })
        : $(document).ready(function () { setTimeout(check_route, 800); });

    /* ── Aide contextuelle RDV (economiq only) ───────────────────────────── */
    function show_rdv_help($target) {
        var key = 'help_rdv_cal';
        if (localStorage.getItem(key) === 'hidden') return;
        if ($target.find('#' + key).length) return;
        var items = [
            {icon:'📅', label:'Nouveau RDV', desc:'Cliquez sur un créneau libre dans le calendrier pour créer un rendez-vous.'},
            {icon:'✏️', label:'Modifier RDV', desc:'Depuis la liste d\'appel, le bouton "Modifier RDV" ouvre le calendrier avec le client déjà sélectionné. La tâche passe en rouge pendant la modification.'},
            {icon:'👤', label:'Client pré-rempli', desc:'Depuis le rapport Rattrapage ou la liste d\'appel, le client, l\'adresse et le secteur sont remplis automatiquement.'},
            {icon:'💾', label:'Enregistrer', desc:'Après avoir choisi le créneau, remplissez les détails et cliquez Enregistrer. Le rapport se rafraîchit ensuite automatiquement.'}
        ];
        var collapsed = localStorage.getItem(key + '_c') === '1';
        var html = items.map(function(it) {
            return "<div style='display:flex;gap:8px;align-items:flex-start;margin:3px 0;'><span style='font-size:14px;flex-shrink:0;'>" + it.icon + "</span><span style='font-size:12px;color:#444;line-height:1.5;'><strong>" + it.label + "</strong> — " + it.desc + "</span></div>";
        }).join('');
        var $b = $("<div id='" + key + "' style='background:linear-gradient(135deg,#e8f5fd,#f0f9ff);border:1px solid #90caf9;border-radius:10px;padding:10px 14px;margin:10px 16px 4px;box-shadow:0 2px 6px rgba(0,100,200,0.08);'>"
            + "<div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;'>"
            + "<strong style='font-size:12px;color:#1565c0;'>💡 Guide — Calendrier RDV</strong>"
            + "<div style='display:flex;gap:12px;'>"
            + "<button class='_ht' style='font-size:11px;color:#1565c0;background:none;border:none;cursor:pointer;padding:0;'>" + (collapsed ? '▶ Afficher' : '▼ Réduire') + "</button>"
            + "<button class='_hc' style='font-size:11px;color:#aaa;background:none;border:none;cursor:pointer;padding:0;'>✕ Fermer</button>"
            + "</div></div>"
            + "<div class='_hb' style='display:" + (collapsed ? "none" : "block") + ";'>" + html + "</div>"
            + "</div>");
        $b.find('._ht').on('click', function() { var $bd = $b.find('._hb'); var v = $bd.is(':visible'); $bd.toggle(); $(this).text(v ? '▶ Afficher' : '▼ Réduire'); localStorage.setItem(key + '_c', v ? '1' : '0'); });
        $b.find('._hc').on('click', function() { localStorage.setItem(key, 'hidden'); $b.fadeOut(200, function() { $b.remove(); }); });
        $target.prepend($b);
    }

    /* ── Step 1: Full-screen calendar overlay ─────────────────────────────── */
    function openOverlay() {
        $('#rdv-cal-overlay').remove();

        // Capture prefill immediately (set by Rattrapage report before calling openOverlay)
        var _overlayPrefill = null;
        try {
            _overlayPrefill = JSON.parse(localStorage.getItem('rdv_prefill') || 'null');
            if (_overlayPrefill) localStorage.removeItem('rdv_prefill');
        } catch(e) {}

        function rdvMsgHandler(ev) {
            if (!ev.data || ev.data.type !== 'rdv_new_doc') return;
            const d = ev.data.doc;
            localStorage.removeItem('rdv_cdn');
            localStorage.removeItem('rdv_edit_task');
            // Open dialog on top of the overlay (dialog z-index raised to 11000)
            setTimeout(function () {
                openRdvDialog(d, function(saved) {
                    if (saved) {
                        // Show spinner on overlay while iframe reloads
                        body.css('opacity', '0.4');
                        hdr.find('#rdv-loading').show();
                        iframe.one('load', function() {
                            body.css('opacity', '1');
                            hdr.find('#rdv-loading').hide();
                        });
                        try { iframe[0].contentWindow.location.reload(); } catch(e) {}
                        // If report is open in background, refresh it
                        if (frappe.query_report && typeof frappe.query_report.refresh === 'function') {
                            setTimeout(function () { frappe.query_report.refresh(); }, 800);
                        }
                    }
                }, _overlayPrefill);
            }, 80);
        }
        window.addEventListener('message', rdvMsgHandler);

        const overlay = $('<div id="rdv-cal-overlay">').css({
            position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
            zIndex: 10000, background: '#fff',
            display: 'flex', flexDirection: 'column',
        });

        const clientLabel = '';

        const hdr = $('<div>').css({
            display: 'flex', alignItems: 'center', padding: '6px 14px',
            background: '#f5f7fa', borderBottom: '1px solid #d1d8dd',
            flexShrink: 0, gap: '10px',
        }).append(
            $('<span>').css({ flex: '1', fontWeight: '600', fontSize: '14px' })
                .html('&#128197; Prendre Rendez-vous'),
            $('<span id="rdv-loading">').css({ fontSize: '12px', color: '#888', display: 'none' })
                .text('Mise à jour...'),
            $('<button class="btn btn-sm btn-default">').html('&#8592; Retour')
                .on('click', function () {
                    window.removeEventListener('message', rdvMsgHandler);
                    overlay.remove();
                    localStorage.removeItem('rdv_cdn');
                    localStorage.removeItem('rdv_edit_task');
                    localStorage.removeItem('rdv_source_tache');
                    // Refresh the Rattrapage report if it's in the background
                    if (frappe.query_report && typeof frappe.query_report.refresh === 'function') {
                        setTimeout(function () { frappe.query_report.refresh(); }, 200);
                    }
                })
        );

        const body = $('<div>').css({ flex: '1', overflow: 'hidden', position: 'relative' });

        const iframe = $('<iframe>').attr({
            src: '/app/tache-de-travail/view/calendar/Calendrier%20de%20travail',
            frameborder: '0',
        }).css({ width: '100%', height: '100%', border: 'none', display: 'block' });

        iframe.on('load', function () {
            try {
                const idoc = iframe[0].contentDocument;
                const iw   = iframe[0].contentWindow;
                if (!idoc) return;

                // Hide Raven + Générer BL via CSS + JS polling
                if (!idoc.getElementById('rdv-s')) {
                    const s = idoc.createElement('style');
                    s.id = 'rdv-s';
                    s.textContent = '[class*="raven"],[id*="raven"]{display:none!important}';
                    idoc.head.appendChild(s);
                }
                function hideGenererBL() {
                    idoc.querySelectorAll('button').forEach(function(b) {
                        if (b.textContent && b.textContent.includes('BL')) {
                            b.style.setProperty('display', 'none', 'important');
                        }
                    });
                }
                hideGenererBL();
                const _blTimer = setInterval(hideGenererBL, 400);
                setTimeout(function(){ clearInterval(_blTimer); }, 15000);

                function fmtDate(d) {
                    if (!d) return '';
                    return d.getFullYear() + '-'
                         + String(d.getMonth() + 1).padStart(2, '0') + '-'
                         + String(d.getDate()).padStart(2, '0') + ' '
                         + String(d.getHours()).padStart(2, '0') + ':'
                         + String(d.getMinutes()).padStart(2, '0') + ':00';
                }

                function sendSlot(start, end) {
                    window.parent.postMessage({
                        type: 'rdv_new_doc',
                        doc: {
                            custom_type_dintervention: 'Entretien',
                            starts_on:                 fmtDate(start),
                            ends_on:                   fmtDate(end),
                        },
                    }, '*');
                }

                function clickSemaine() {
                    const jq = iw.jQuery || iw.$;
                    if (!jq) return false;
                    const btn = jq('button').filter(function () {
                        return jq(this).text().trim() === 'Semaine';
                    });
                    if (btn.length && !btn.hasClass('active')) { btn.trigger('click'); }
                    return btn.length > 0;
                }

                function patchCalendar() {
                    const calList = iw.cur_list;
                    if (!calList) return false;
                    if (calList._rdv_patched) return true;

                    try {
                        calList.calendar.setOption('selectable', true);
                        calList.calendar.setOption('select', function (info) {
                            sendSlot(info.start, info.end);
                        });
                    } catch (e) {}

                    const origNewEvent = calList.new_event && calList.new_event.bind(calList);
                    if (origNewEvent) {
                        calList.new_event = function (date) { sendSlot(date, null); };
                    }

                    calList._rdv_patched = true;
                    return true;
                }

                let tries = 0;
                const t = setInterval(function () {
                    const ok1 = clickSemaine();
                    const ok2 = patchCalendar();
                    if ((ok1 && ok2) || ++tries > 30) clearInterval(t);
                }, 200);
            } catch (e) {
                console.warn('[RDV-FAB] iframe patch error', e);
            }
        });

        body.append(iframe);
        overlay.append(hdr, body);
        $('body').append(overlay);
    }

    /* ── Step 2: RDV details dialog (opened after slot selection) ──────────── */
    function openRdvDialog(d, onDismiss, prefill) {
        var _addresses = (prefill && prefill._addresses) || [];
        var _secteur   = (prefill && prefill.secteur)    || '';
        var _googleMap = '';
        var _saved     = false;

        var dlg = new frappe.ui.Dialog({
            title: '📅 Nouveau Rendez-vous',
            fields: [
                {
                    fieldname: 'custom_client', fieldtype: 'Link', label: 'Client',
                    options: 'Customer', reqd: 1,
                    get_query: function () {
                        return { query: 'customization_app.api.search_customer_all' };
                    },
                    onchange: function () {
                        var cli = dlg.get_value('custom_client');
                        if (!cli) { _addresses = []; _secteur = ''; dlg.set_value('secteur', ''); return; }
                        frappe.call({
                            method: 'customization_app.api.get_customer_info_all',
                            args: { customer: cli },
                            callback: function (r) {
                                var info = r.message || {};
                                _addresses = info.addresses || [];
                                _secteur   = info.secteur   || '';
                                dlg.set_value('secteur', _secteur);
                                // Rebuild address select options
                                var af = dlg.get_field('select_address');
                                af.df.options = [''].concat(_addresses.map(function(a){ return a.name; })).join('\n');
                                af.refresh();
                                _googleMap = '';
                                dlg.set_value('select_address', '');
                                dlg.set_value('details_adresse', '');
                            },
                        });
                    },
                },
                { fieldtype: 'Column Break' },
                { fieldname: 'custom_type_dintervention', fieldtype: 'Select',
                  label: "Type d'Intervention",
                  options: 'Entretien\nInstallation\nRéparation\nLivraison\nVisite\nAutre', reqd: 1 },
                { fieldtype: 'Section Break' },
                { fieldname: 'custom_choix_du_staff', fieldtype: 'Link',
                  label: 'Choix du Staff', options: 'Employee', reqd: 1 },
                { fieldtype: 'Column Break' },
                { fieldname: 'starts_on', fieldtype: 'Datetime', label: 'Commence le', reqd: 1 },
                { fieldtype: 'Section Break' },
                { fieldname: 'secteur', fieldtype: 'Data', label: 'Secteur', read_only: 1 },
                {
                    fieldname: 'select_address', fieldtype: 'Select', label: 'Adresse',
                    options: '',
                    onchange: function () {
                        var addrName = dlg.get_value('select_address');
                        if (!addrName) { _googleMap = ''; dlg.set_value('details_adresse', ''); return; }
                        var a = _addresses.find(function (x) { return x.name === addrName; }) || {};
                        _googleMap = a.custom_lien_google_map || '';
                        var lines = [a.address_line1, a.address_line2, a.city].filter(Boolean);
                        var reg   = [a.pincode, a.state, a.country].filter(Boolean).join(', ');
                        if (reg) lines.push(reg);
                        dlg.set_value('details_adresse',
                            (_secteur ? 'Secteur: ' + _secteur + '\n' : '') + lines.join('\n'));
                    },
                },
                { fieldname: 'details_adresse', fieldtype: 'Small Text',
                  label: 'Détails adresse', read_only: 1 },
                { fieldtype: 'Section Break' },
                { fieldname: 'note', fieldtype: 'Small Text', label: 'Note' },
            ],
            primary_action_label: 'Enregistrer le RDV',
            primary_action: function (vals) {
                var cli      = vals.custom_client             || '';
                var type_int = vals.custom_type_dintervention || '';
                var staffId  = vals.custom_choix_du_staff    || '';
                var icon     = INTERVENTION_ICONS[type_int]  || '☕ ';
                var starts   = vals.starts_on                || '';
                var ends     = d.ends_on                     || '';

                if (starts && !ends) {
                    var dur = INTERVENTION_DURATIONS[type_int] || 120;
                    var m   = new Date(starts.replace(' ', 'T'));
                    m.setMinutes(m.getMinutes() + dur);
                    var pad = function (n) { return String(n).padStart(2, '0'); };
                    ends = m.getFullYear() + '-' + pad(m.getMonth() + 1) + '-' + pad(m.getDate())
                         + ' ' + pad(m.getHours()) + ':' + pad(m.getMinutes()) + ':00';
                }

                function doSave(employeeName) {
                    var sec   = _secteur || '';
                    var titre = (sec ? sec + '\n' : '')
                              + icon + type_int + ': Client: ' + cli
                              + '\n' + (employeeName || '');
                    var doc = {
                        doctype:                   'Tache de travail',
                        custom_client:             cli,
                        custom_type_dintervention: type_int,
                        custom_choix_du_staff:     staffId,
                        'custom_employ\u00e9':     employeeName || '',
                        starts_on:                 starts,
                        ends_on:                   ends,
                        select_address:            vals.select_address || '',
                        details_adresse:           vals.details_adresse || '',
                        google_map:                _googleMap,
                        secteur:                   sec,
                        subject:                   vals.note || '',
                        titre:                     titre,
                    };
                    dlg.hide();
                    _saved = true;
                    if (onDismiss) { onDismiss(true); onDismiss = null; }
                    frappe.call({
                        method: 'frappe.client.save',
                        args: { doc: doc },
                        callback: function (r) {
                            if (r.message) {
                                frappe.show_alert({ message: 'Rendez-vous créé ✅', indicator: 'green' }, 4);
                                // If called from Rattrapage report: mark source task as planifié
                                var srcTache = localStorage.getItem('rdv_source_tache');
                                if (srcTache) {
                                    frappe.call({
                                        method: 'customization_app.api.marquer_tache_rattrapee',
                                        args: { tache: srcTache, raison: 'planifié' },
                                    });
                                    localStorage.removeItem('rdv_source_tache');
                                }
                            }
                        },
                    });
                }

                if (staffId) {
                    frappe.db.get_value('Employee', staffId, 'employee_name')
                        .then(function (r) { doSave((r.message || {}).employee_name || ''); });
                } else {
                    doSave('');
                }
            },
        });

        dlg.set_value('custom_type_dintervention', d.custom_type_dintervention || 'Entretien');
        if (d.starts_on) dlg.set_value('starts_on', d.starts_on);
        // Secteur + addresses set before show (Data fields render fine before show)
        if (prefill && prefill.custom_client) {
            dlg.set_value('secteur', _secteur);
            if (_addresses.length) {
                var af = dlg.get_field('select_address');
                af.df.options = [''].concat(_addresses.map(function (a) { return a.name; })).join('\n');
                af.refresh();
            }
        }

        // Raise dialog above overlay (z-index 10000)
        dlg.$wrapper.css('z-index', '11000');
        dlg.$wrapper.find('.modal-backdrop').css('z-index', '10999');
        // If user closes with X without saving
        dlg.$wrapper.on('hidden.bs.modal', function() {
            if (onDismiss) { onDismiss(_saved); onDismiss = null; }
        });
        dlg.show();

        // Link fields need DOM + bypass server permission validation (set input directly)
        if (prefill && prefill.custom_client) {
            setTimeout(function () {
                var f = dlg.get_field('custom_client');
                if (f && f.$input) {
                    f.$input.val(prefill.custom_client);
                    f.value      = prefill.custom_client;
                    f.last_value = prefill.custom_client;
                }
            }, 200);
        }
    }

    // Expose openOverlay globally so reports and other pages can trigger it
    window.rdvLibre_openOverlay = openOverlay;

})();
