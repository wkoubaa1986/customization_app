frappe.provide("frappe.views");

frappe.views.CalendarViewList = class CalendarViewList extends frappe.views.Calendar {
    constructor(options) {
        // console.log("Custom Calendar View List Initialization started...");
        super(options);  // Call the original constructor

        // Only initialize if the calendar name matches
        if (this.list_view.calendar_name === 'Calendrier de travail') {
            // console.log("Custom Calendar View List for 'Calendrier de travail' initialized...");

            this.get_events_method = 'customization_app.api.get_custom_tache_events';  // Custom method to fetch events
            this.field_map = {
                "start": "starts_on",
                "end": "ends_on",
                "id": "name",
                "title": "titre",
                "color": "color",
                "allDay": "all_day"
            };


            
        }
    }
    // // Ensure eventRender is correctly overridden here
    // eventRender(event, element) {
    //     console.log("Rendering event:", event);  // Log to check if it's triggered

    //     // Call the original eventRender from the parent class (if needed)
    //     super.eventRender(event, element);

    //     // Apply custom styling if event has custom_reservation_app === 1
    //     if (event.custom_reservation_app === 1) {
    //         console.log("Applying custom styles for event:", event.id);

    //         // Apply dashed background style and other custom styles
    //         element.css({
    //             "background-image": 'linear-gradient(45deg, rgba(0, 0, 0, 0.1) 25%, transparent 25%, transparent 50%, rgba(0, 0, 0, 0.1) 50%, rgba(0, 0, 0, 0.1) 75%, transparent 75%, transparent)',
    //             "background-size": "10px 10px",  // Adjust the size of the dashes
    //             "border-color": "#28a745",  // Green border color
    //             "border-width": "3px",  // Thicker border (3px)
    //             "border-style": "solid",  // Solid border style
    //             "color": "#ffffff",  // Text color (white) for visibility
    //         });
    //     }
    // }
    setup_options(defaults) {
		var me = this;
		defaults.meridiem = "false";
		this.cal_options = {
			locale: frappe.boot.lang,
			header: {
				left: "prev, title, next",
				right: "today, month, agendaWeek, agendaDay",
			},
			editable: true,
			selectable: true,
			selectHelper: true,
			forceEventDuration: true,
			displayEventTime: true,
			defaultView: defaults.defaultView,
			weekends: defaults.weekends,
			nowIndicator: true,
			buttonText: {
				today: __("Today"),
				month: __("Month"),
				week: __("Week"),
				day: __("Day"),
			},
			events: function (start, end, timezone, callback) {
				return frappe.call({
					method: me.get_events_method || "frappe.desk.calendar.get_events",
					type: "GET",
					args: me.get_args(start, end),
					callback: function (r) {
						var events = r.message || [];
						events = me.prepare_events(events);
						callback(events);
					},
				});
			},
			displayEventEnd: true,
			eventRender: function (event, element) {
				element.attr("title", event.tooltip);
                if (me.list_view.calendar_name === 'Calendrier de travail') {

                    let originalColor = event.backgroundColor || '#FF6347'; 
                    if (originalColor === '#AA00AA') {
                        // If the background color is '#AA00AA', make the text white
                        // console.log("Applying white text color for event:", event.id);
                        element.css("color", "#ffffff");  // Set text color to white
                    }
                    element.css({
                        "border-color": originalColor,  // Green border color
                        "border-width": "3px",  // Thicker border (3px)
                        "border-style": "solid",  // Solid border style
                        });
                    if (event.color) {
                        // console.log("Applying custom background color with opacity for event:", event.id);
                        const status_opaque = ["Completed","Cancelled"];
                        let opacity = 0.55; // Set the desired opacity level (0.0 to 1.0)
                        if (status_opaque.includes(event.status)) {
                            opacity = 0.8;
                        }
                        // If color is in hex format, convert it to RGBA with opacity
                        if (event.color.charAt(0) === '#') {
                            // Convert hex to rgba (set alpha/opacity to 0.3)
                            originalColor = hexToRgba(originalColor, opacity); // Convert to RGBA with 30% opacity
                            // element.css({
                            //     "background-color": rgbaColor,
                            // });
                        } else if (event.color.startsWith('rgba')) {
                            // If color is already in rgba, simply add more opacity
                            let rgbaValues = originalColor.match(/^rgba\((\d+), (\d+), (\d+), (\d(\.\d+)?)\)$/);
                            if (rgbaValues) {
                                // Get the original rgba values and set new opacity
                                let newOpacity = opacity; // Change opacity here
                                originalColor = `rgba(${rgbaValues[1]}, ${rgbaValues[2]}, ${rgbaValues[3]}, ${newOpacity})`;
                            
                            }
                        }
                        element.css({
                            "background-color": originalColor,
                        });
                    }        // Apply custom styling if event has custom_reservation_app === 1

                    
                    if (event.custom_reservation_app === 1) {
                        // Apply dashed background style with grey hachuré and keep original color with transparency
                        // Use the event's original color or fallback to red
                        // console.log("Applying custom styles for event:", event.title);
                        element.css({
                            "background-image": `linear-gradient(45deg, rgba(128, 128, 128, 0.2) 25%, transparent 25%, transparent 50%, rgba(128, 128, 128, 0.2) 50%, rgba(128, 128, 128, 0.2) 75%, transparent 75%, transparent)`,
                            "background-size": "6px 6px",  // Adjust the size of the dashes
                            "background-color": originalColor, 
                        });
                    }
                    let tooltipParts = [];
                    const defaultRapport = "Indiquez vos remarques sur l'intervention et le client:";
                    if (event.title) tooltipParts.push(`${event.title}`);
                    if (event.custom_type_dintervention) tooltipParts.push(`**Type intervention:** \n ${event.custom_type_dintervention}`);
                    if (event.custom_employé) tooltipParts.push(`**Employé(e):** \n ${event.custom_employé}`);
                    if (event.nom_client) tooltipParts.push(`**Client:** \n ${event.nom_client}`);
                    if (event.tel) tooltipParts.push(`**Liste Téléphones:**\n ${event.tel}`);
                    if (event.info_secteur) tooltipParts.push(`**Info Secteur:**\n ${event.secteur}\n ${event.info_secteur}`);
                    if (event.details_adresse) {
                        let adresseText = `**Adresse:**\n${event.details_adresse}`;
                        if (event.google_map) {
                            adresseText += `\n${event.google_map}`;
                        }
                        tooltipParts.push(adresseText);
                    }
                    if (event.subject) tooltipParts.push(`**Sujet:** \n ${event.subject}`);
                    if (event.raison_annulation) tooltipParts.push(`**Raison annulation:** \n ${event.raison_annulation}`);
                    if (event.rapport_visite && event.rapport_visite.trim() !== defaultRapport) {
                        tooltipParts.push(`**Rapport visite:**\n${event.rapport_visite}`);
                    }

                    let tooltipText = tooltipParts.join('\n\n');
                    element.attr("title", tooltipText);
                    // Écriture réduite sur tout le calendrier (titre + heure)
                    element.find('.fc-title, .fc-time').css("font-size", "10px");
                    if (event.all_day===1) {
                        const title = element.find('.fc-title');


                        // If the title overflows, reduce the font size
                        title.css({
                            "font-size": "9px",  // Decrease the font size
                            "white-space": "nowrap",  // Prevent text wrapping
                            "overflow": "hidden",  // Hide overflow text
                            "text-overflow": "ellipsis",  // Add ellipsis if text overflows
                        });
                    }


                }
            },
			eventClick: function (event) {
				// edit event description or delete
				var doctype = event.doctype || me.doctype;
				if (frappe.model.can_read(doctype)) {
					frappe.set_route("Form", doctype, event.name);
				}
			},
			eventDrop: function (event, delta, revertFunc) {
				me.update_event(event, revertFunc);
			},
			eventResize: function (event, delta, revertFunc) {
				me.update_event(event, revertFunc);
			},
            viewRender: function(view, element) {
                if (me.list_view && me.list_view.calendar_name === 'Calendrier de travail') {
                    setTimeout(() => _inject_generer_bl_btn(), 150);
                }
            },
			select: function (startDate, endDate, jsEvent, view) {
				if (view.name === "month" && endDate - startDate === 86400000) {
					// detect single day click in month view
					return;
				}

				var event = frappe.model.get_new_doc(me.doctype);

				event[me.field_map.start] = me.get_system_datetime(startDate);

				if (me.field_map.end) event[me.field_map.end] = me.get_system_datetime(endDate);

				if (me.field_map.allDay) {
					var all_day = startDate._ambigTime && endDate._ambigTime ? 1 : 0;

					event[me.field_map.allDay] = all_day;

					if (all_day)
						event[me.field_map.end] = me.get_system_datetime(
							moment(endDate).subtract(1, "s")
						);
				}

				frappe.set_route("Form", me.doctype, event.name);
			},
			dayClick: function (date, jsEvent, view) {
				if (view.name === "month") {
					const $date_cell = $("td[data-date=" + date.format("YYYY-MM-DD") + "]");

					if ($date_cell.hasClass("date-clicked")) {
						me.$cal.fullCalendar("changeView", "agendaDay");
						me.$cal.fullCalendar("gotoDate", date);
						me.$wrapper.find(".date-clicked").removeClass("date-clicked");

						// update "active view" btn
						me.$wrapper.find(".fc-month-button").removeClass("active");
						me.$wrapper.find(".fc-agendaDay-button").addClass("active");
					}

					me.$wrapper.find(".date-clicked").removeClass("date-clicked");
					$date_cell.addClass("date-clicked");
				}
				return false;
			},
		};

		if (this.options) {
			$.extend(this.cal_options, this.options);
		}
	}
};

// Helper function to convert hex to rgba with given opacity
function hexToRgba(hex, opacity) {
    // Ensure it's a valid hex color
    if (hex.charAt(0) === '#') {
        hex = hex.substring(1);
    }
    if (hex.length === 3) {
        hex = hex.split('').map(function (char) {
            return char + char;
        }).join('');
    }
    const r = parseInt(hex.substring(0, 2), 16);
    const g = parseInt(hex.substring(2, 4), 16);
    const b = parseInt(hex.substring(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${opacity})`;
}
// Replace the original Calendar with the custom version
frappe.views.Calendar = frappe.views.CalendarViewList;

// ── Bouton "Générer BL" dans le calendrier Tache de travail ──────────────────

function _inject_generer_bl_btn() {
    // Masquer pour l'utilisateur partenaire
    if (frappe.session.user === 'economiqaquasolutions23@gmail.com') return;

    // Ne pas injecter deux fois
    if (document.getElementById("btn-generer-bl")) return;

    // Trouver le conteneur de la date dans le calendrier
    const calHeader = document.querySelector(".fc-header-toolbar .fc-left, .fc-toolbar .fc-left, .fc-toolbar-chunk");
    const target = calHeader || document.querySelector(".fc-toolbar");
    if (!target) {
        // Réessayer si le calendrier n'est pas encore rendu
        setTimeout(() => _inject_generer_bl_btn(), 600);
        return;
    }

    const btn = document.createElement("button");
    btn.id = "btn-generer-bl";
    btn.innerHTML = "🚚 Générer BL";
    btn.style.cssText = `
        background: #dc2626; color: #fff; border: none; border-radius: 6px;
        padding: 6px 14px; font-weight: 700; font-size: 13px; cursor: pointer;
        margin-left: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.2);
    `;
    btn.onclick = function() { _open_generer_bl_dialog(); };

    // Insérer après le bouton "Aujourd'hui"
    const todayBtn = document.querySelector(".fc-today-button");
    if (todayBtn && todayBtn.parentNode) {
        todayBtn.parentNode.insertBefore(btn, todayBtn.nextSibling);
    } else {
        target.appendChild(btn);
    }
}

function _open_generer_bl_dialog() {
    const today = frappe.datetime.get_today();

    const d = new frappe.ui.Dialog({
        title: "🚚 Générer les Bons de Livraison",
        size: "large",
        fields: [
            {
                fieldname: "date",
                fieldtype: "Date",
                label: "Date des interventions",
                reqd: 1,
                default: today,
                onchange: function() {
                    _load_employees(d);
                },
            },
            { fieldtype: "Section Break", fieldname: "sb_emp", label: "Tâches ouvertes" },
            {
                fieldname: "employees_html",
                fieldtype: "HTML",
                options: "<div class=\"genbl-emp-wrap\"></div>",
            },
        ],
        primary_action_label: "Générer & Imprimer",
        primary_action: function() {
            _do_generer_bl(d);
        },
    });

    d.show();

    // Charger les employés après que le dialog est rendu avec la valeur par défaut
    setTimeout(() => {
        if (!d.get_value("date")) {
            d.set_value("date", today);
        }
        _load_employees(d);
    }, 800);
}

// Pastille indiquant ce qui sera produit pour une tâche
function _genbl_badge(prevu) {
    const styles = {
        reel:    ["#dcfce7", "#166534", "BL commande"],
        virtuel: ["#dbeafe", "#1e40af", "Bon de chargement"],
        ignore:  ["#f3f4f6", "#6b7280", "Ignorée"],
    };
    const [bg, fg, label] = styles[prevu] || styles.ignore;
    return `<span style="background:${bg};color:${fg};padding:1px 6px;border-radius:10px;
            font-size:11px;white-space:nowrap">${label}</span>`;
}

// Pastille des livraisons Aramex : parties par transporteur, décochées par défaut.
function _genbl_badge_aramex() {
    return `<span style="background:#ffedd5;color:#9a3412;padding:1px 6px;border-radius:10px;
            font-size:11px;white-space:nowrap;margin-right:4px">Aramex</span>`;
}

function _load_employees(dialog) {
    const date = dialog.get_value("date");
    if (!date) return;

    const $wrap = dialog.fields_dict.employees_html.$wrapper;
    $wrap.html("<i>Chargement...</i>");

    frappe.call({
        method: "customization_app.generer_bl.get_taches_par_date",
        args: { date },
        callback: function(r) {
            const employees = (r.message || []);
            if (!employees.length) {
                $wrap.html(`<div style="color:#888;padding:8px">Aucune tâche ouverte trouvée pour le ${date}</div>`);
                return;
            }

            dialog._bl_employees = employees;

            let html = `<div style="max-height:420px;overflow:auto">`;

            employees.forEach((emp, idx) => {
                const nbImprimables = emp.taches.filter(t => t.prevu !== "ignore").length;
                const nbAramex = emp.taches.filter(t => t.aramex).length;
                // Coché par défaut : imprimable ET pas une livraison Aramex.
                const nbCoches = emp.taches.filter(t => t.prevu !== "ignore" && !t.aramex).length;

                html += `<div style="border:1px solid #e5e7eb;border-radius:6px;margin-bottom:10px">
                    <div style="display:flex;align-items:center;gap:10px;padding:8px 10px;background:#f9fafb;
                                border-bottom:1px solid #e5e7eb">
                        <input type="checkbox" class="genbl-emp-chk" data-emp="${idx}"
                               ${nbCoches ? "checked" : ""} style="width:16px;height:16px">
                        <div style="flex:1">
                            <strong>${frappe.utils.escape_html(emp.employee_name)}</strong>
                            <small style="color:#888"> — ${nbImprimables}/${emp.taches.length} tâche(s) imprimable(s)</small>
                            ${nbAramex ? `<small style="color:#9a3412"> · ${nbAramex} Aramex décochée(s)</small>` : ""}
                        </div>
                        <select data-emp="${idx}" class="genbl-vehicle-sel"
                                style="width:190px;padding:4px;border:1px solid #d1d5db;border-radius:4px">
                            <option value="">-- Véhicule --</option>
                        </select>
                    </div>
                    <table style="width:100%;border-collapse:collapse;font-size:12.5px">`;

                emp.taches.forEach((t, tidx) => {
                    const ignore = t.prevu === "ignore";
                    // Aramex : proposée mais décochée — l'utilisateur peut la cocher.
                    const coche = !ignore && !t.aramex;
                    html += `<tr style="border-bottom:1px solid #f3f4f6;${ignore ? "opacity:.55" : ""}">
                        <td style="padding:5px 10px;width:28px">
                            <input type="checkbox" class="genbl-tache-chk" data-emp="${idx}" data-tache="${tidx}"
                                   value="${t.name}" ${coche ? "checked" : ""} ${ignore ? "disabled" : ""}
                                   style="width:14px;height:14px">
                        </td>
                        <td style="padding:5px 6px;width:48px;color:#666">${t.heure || ""}</td>
                        <td style="padding:5px 6px">${frappe.utils.escape_html(t.client || "—")}</td>
                        <td style="padding:5px 6px;color:#666">${frappe.utils.escape_html(t.type || "")}</td>
                        <td style="padding:5px 10px;text-align:right;white-space:nowrap">${
                            t.aramex ? _genbl_badge_aramex() : ""}${_genbl_badge(t.prevu)}</td>
                    </tr>`;
                });

                html += `</table></div>`;
            });

            html += `</div>`;
            $wrap.html(html);

            // Cocher/décocher un employé bascule toutes ses tâches imprimables
            $wrap.find(".genbl-emp-chk").on("change", function() {
                const idx = this.getAttribute("data-emp");
                const etat = this.checked;
                $wrap.find(`.genbl-tache-chk[data-emp="${idx}"]`).each(function() {
                    if (!this.disabled) this.checked = etat;
                });
            });

            // Charger les options véhicule pour chaque employé
            frappe.db.get_list("Vehicle", {
                fields: ["name", "license_plate", "model"],
                limit: 50,
            }).then(vehicles => {
                employees.forEach((emp, idx) => {
                    const sel = $wrap.find(`.genbl-vehicle-sel[data-emp="${idx}"]`)[0];
                    if (!sel) return;
                    vehicles.forEach(v => {
                        const opt = document.createElement("option");
                        opt.value = v.name;
                        opt.textContent = `${v.name} - ${v.model || ""}`;
                        if (v.name === emp.default_vehicle) opt.selected = true;
                        sel.appendChild(opt);
                    });
                });
            });
        },
    });
}

function _do_generer_bl(dialog) {
    const date = dialog.get_value("date");
    const employees = dialog._bl_employees || [];

    if (!employees.length) {
        frappe.msgprint("Aucun employé à traiter.");
        return;
    }

    const $wrap = dialog.fields_dict.employees_html.$wrapper;

    // Une entrée par employé, avec la liste des tâches réellement cochées
    const selections = [];
    employees.forEach((emp, idx) => {
        const taches = [];
        $wrap.find(`.genbl-tache-chk[data-emp="${idx}"]`).each(function() {
            if (this.checked && !this.disabled) taches.push(this.value);
        });
        if (!taches.length) return;
        const sel = $wrap.find(`.genbl-vehicle-sel[data-emp="${idx}"]`)[0];
        selections.push({
            employee: emp.employee,
            vehicle: sel ? sel.value : (emp.default_vehicle || ""),
            taches: taches,
        });
    });

    if (!selections.length) {
        frappe.msgprint("Cochez au moins une tâche.");
        return;
    }

    dialog.hide();
    frappe.dom.freeze("Génération des bons de livraison...");

    frappe.call({
        method: "customization_app.generer_bl.generer_et_imprimer",
        args: { date, selections: JSON.stringify(selections) },
        callback: function(r) {
            frappe.dom.unfreeze();
            const res = r.message;
            if (!res) {
                frappe.msgprint({ title: "Générer BL", message: "Aucune réponse du serveur.", indicator: "red" });
                return;
            }
            // window.open() hors geste utilisateur : Chrome bloque la popup.
            // On note l'échec pour proposer un lien cliquable dans le compte-rendu.
            let bloque = false;
            if (res.file_url) {
                const onglet = window.open(res.file_url, "_bl_print");
                bloque = !onglet || onglet.closed || typeof onglet.closed === "undefined";
            }
            _afficher_rapport(res, bloque);
        },
        error: function() {
            frappe.dom.unfreeze();
            frappe.msgprint({
                title: "Générer BL",
                message: "La génération a échoué. Aucun bon de livraison n'a été supprimé.",
                indicator: "red",
            });
        },
    });
}

function _afficher_rapport(res, popup_bloquee) {
    const rapport = res.rapport || [];

    const lignes = rapport.map(e => {
        const couleurs = { genere: "#166534", ignore: "#b45309", erreur: "#b91c1c" };
        const libelles = { genere: "Généré", ignore: "Ignoré", erreur: "Erreur" };
        return `<tr style="border-bottom:1px solid #f3f4f6">
            <td style="padding:4px 8px;white-space:nowrap">${frappe.utils.escape_html(e.employee_name || "")}</td>
            <td style="padding:4px 8px">${frappe.utils.escape_html(e.client || "—")}</td>
            <td style="padding:4px 8px;color:#666">${frappe.utils.escape_html(e.type || "")}</td>
            <td style="padding:4px 8px;font-weight:600;color:${couleurs[e.statut] || "#374151"}">${libelles[e.statut] || e.statut}</td>
            <td style="padding:4px 8px;color:#555">${frappe.utils.escape_html(e.message || "")}</td>
        </tr>`;
    }).join("");

    let entete = `<p><strong>${res.nb_generes}</strong> généré(s) · `
        + `<strong>${res.nb_ignores}</strong> ignoré(s) · `
        + `<strong>${res.nb_erreurs}</strong> en erreur.</p>`;

    if (res.erreur_pdf) {
        entete += `<p style="color:#b91c1c"><strong>${frappe.utils.escape_html(res.erreur_pdf)}</strong></p>`;
    } else if (!res.file_url) {
        entete += `<p style="color:#b45309">Aucun document à imprimer.</p>`;
    } else {
        // Lien de repli, en plus du bouton principal du dialogue.
        entete += `<p style="margin:10px 0">
            <a href="${res.file_url}" target="_blank" rel="noopener"
               style="display:inline-block;background:#dc2626;color:#fff;padding:8px 16px;
                      border-radius:6px;text-decoration:none;font-weight:600">
               📄 Ouvrir le PDF à imprimer</a>
            ${popup_bloquee
                ? `<span style="margin-left:10px;color:#b45309">Le navigateur a bloqué l'ouverture
                   automatique — utilisez ce bouton.</span>`
                : ""}
        </p>`;
    }

    // Dialogue dédié, et NON frappe.msgprint : ce dernier réutilise un unique
    // msg_dialog partagé et empile les messages avec un <hr>. Les « BL … en
    // attente de validation magasin » émis par le Server Script arrivent avant
    // le callback, ce qui reléguait le compte-rendu et le bouton PDF sous la
    // zone visible. Le bouton principal ouvre en plus le PDF sur un vrai clic,
    // donc sans blocage de popup.
    const dlg = new frappe.ui.Dialog({
        title: "Générer BL — compte-rendu",
        size: "large",
        fields: [{ fieldtype: "HTML", fieldname: "corps" }],
    });

    dlg.fields_dict.corps.$wrapper.html(
        entete + `<div style="max-height:340px;overflow:auto">
            <table style="width:100%;border-collapse:collapse;font-size:12.5px">
                <thead><tr style="background:#f3f4f6">
                    <th style="padding:5px 8px;text-align:left">Employé</th>
                    <th style="padding:5px 8px;text-align:left">Client</th>
                    <th style="padding:5px 8px;text-align:left">Type</th>
                    <th style="padding:5px 8px;text-align:left">Statut</th>
                    <th style="padding:5px 8px;text-align:left">Détail</th>
                </tr></thead>
                <tbody>${lignes}</tbody>
            </table></div>`
    );

    if (res.file_url) {
        dlg.set_primary_action("📄 Ouvrir le PDF à imprimer", function() {
            window.open(res.file_url, "_bl_print");
        });
    }

    dlg.show();
}
