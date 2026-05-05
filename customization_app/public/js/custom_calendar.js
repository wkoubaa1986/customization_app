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
                    if (event.all_day===1) {
                        const title = element.find('.fc-title');

                    
                        // If the title overflows, reduce the font size
                        title.css({
                            "font-size": "9 px",  // Decrease the font size
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
            // Add custom CSS for "All Day" section, but only in agendaDay or agendaWeek views
            // viewRender: function(view, element) {
            //     console.log("Rendering view:", view.name); // Log the current view type

            //     // Apply the styles only for the agendaWeek or agendaDay views
            //     if (view.name === "agendaWeek" || view.name === "agendaDay") {
            //         console.log("Customizing 'All Day' Section for Weekly and Daily Views");
            //                  // Find the "All Day" section
            //         const allDaySection = element.find('.fc-day-grid');

            //         // Check if the element is found
            //         if (allDaySection.length) {
            //             // Increase the height and font size of the 'All Day' section
            //             allDaySection.css({
            //                 // "font-size": "18px",  // Increase font size for the header
            //                 // "padding": "20px 0",  // Increase padding to make the area taller
            //                 "height": "100px",  // Set the height of the 'All Day' section
            //                 // "background-color": "#f2f2f2",  // Optional: Add a background color
            //             });
            //         } else {
            //             console.log("Could not find the All Day section.");
            //         }
            //                         // Find and modify the widget content width
            //         const widgetContent = element.find('.fc-widget-content');
            //         if (widgetContent.length) {
            //             widgetContent.css({
            //                 "height": "100px",  // Set the height for the widget content
            //                 "margin": "0 auto",  // Center the content
            //             });
            //         } else {
            //             console.log("Could not find the widget content section.");
            //         }
            // }
            // },
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
frappe.listview_settings["Tache de travail"] = frappe.listview_settings["Tache de travail"] || {};
frappe.listview_settings["Tache de travail"].onload = function(listview) {
    // Attendre que la toolbar de la vue calendrier soit prête
    setTimeout(function() {
        _inject_generer_bl_btn(listview);
    }, 800);
};

function _inject_generer_bl_btn(listview) {
    // Ne pas injecter deux fois
    if (document.getElementById("btn-generer-bl")) return;

    // Trouver le conteneur de la date dans le calendrier
    const calHeader = document.querySelector(".fc-header-toolbar .fc-left, .fc-toolbar .fc-left, .fc-toolbar-chunk");
    const target = calHeader || document.querySelector(".fc-toolbar");
    if (!target) {
        // Réessayer si le calendrier n'est pas encore rendu
        setTimeout(() => _inject_generer_bl_btn(listview), 600);
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
        fields: [
            {
                fieldname: "date",
                fieldtype: "Date",
                label: "Date des interventions",
                reqd: 1,
                default: today,
            },
            { fieldtype: "Section Break", fieldname: "sb_emp", label: "Employés avec tâches ouvertes" },
            {
                fieldname: "employees_html",
                fieldtype: "HTML",
                options: "<div id='genbl-employees-container'><i>Sélectionnez une date...</i></div>",
            },
        ],
        primary_action_label: "Générer & Imprimer",
        primary_action: function() {
            _do_generer_bl(d);
        },
    });

    d.show();

    // Charger les employés dès l'ouverture
    d.fields_dict.date.df.onchange = function() {
        _load_employees(d);
    };
    setTimeout(() => _load_employees(d), 200);
}

function _load_employees(dialog) {
    const date = dialog.get_value("date");
    if (!date) return;

    const container = document.getElementById("genbl-employees-container");
    if (!container) return;
    container.innerHTML = "<i>Chargement...</i>";

    frappe.call({
        method: "customization_app.generer_bl.get_taches_par_date",
        args: { date },
        callback: function(r) {
            const employees = (r.message || []);
            if (!employees.length) {
                container.innerHTML = `<div style="color:#888;padding:8px">Aucune tâche ouverte trouvée pour le ${date}</div>`;
                return;
            }

            // Stocker pour utilisation lors de la génération
            dialog._bl_employees = employees;

            let html = `<table style="width:100%;border-collapse:collapse;font-size:13px">
                <thead><tr style="background:#f3f4f6">
                    <th style="padding:6px 8px;text-align:left">Employé</th>
                    <th style="padding:6px 8px;text-align:center">Tâches</th>
                    <th style="padding:6px 8px;text-align:left">Véhicule</th>
                    <th style="padding:6px 8px;text-align:center">Générer</th>
                </tr></thead><tbody>`;

            employees.forEach((emp, idx) => {
                const nbTaches = emp.taches.length;
                const types = [...new Set(emp.taches.map(t => t.type || "?"))].join(", ");

                html += `<tr style="border-bottom:1px solid #e5e7eb">
                    <td style="padding:6px 8px">
                        <strong>${emp.employee_name}</strong><br>
                        <small style="color:#888">${types}</small>
                    </td>
                    <td style="padding:6px 8px;text-align:center">${nbTaches}</td>
                    <td style="padding:6px 8px">
                        <select id="vehicle-${idx}" style="width:100%;padding:4px;border:1px solid #d1d5db;border-radius:4px">
                            <option value="">-- Véhicule --</option>
                        </select>
                    </td>
                    <td style="padding:6px 8px;text-align:center">
                        <input type="checkbox" id="chk-${idx}" checked style="width:16px;height:16px">
                    </td>
                </tr>`;
            });

            html += "</tbody></table>";
            container.innerHTML = html;

            // Charger les options véhicule pour chaque employé
            frappe.db.get_list("Vehicle", {
                fields: ["name", "license_plate", "model"],
                limit: 50,
            }).then(vehicles => {
                employees.forEach((emp, idx) => {
                    const sel = document.getElementById(`vehicle-${idx}`);
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

    // Collecter les sélections
    const selected = [];
    employees.forEach((emp, idx) => {
        const chk = document.getElementById(`chk-${idx}`);
        if (!chk || !chk.checked) return;
        const sel = document.getElementById(`vehicle-${idx}`);
        const vehicle = sel ? sel.value : (emp.default_vehicle || "");
        selected.push({ employee: emp.employee, vehicle });
    });

    if (!selected.length) {
        frappe.msgprint("Cochez au moins un employé.");
        return;
    }

    dialog.hide();

    frappe.show_progress("Génération des BL...", 0, selected.length);

    let done = 0;
    const allDns = [];

    function processNext() {
        if (done >= selected.length) {
            frappe.hide_progress();
            if (allDns.length) {
                _print_all_dns(allDns);
            } else {
                frappe.msgprint("Aucun BL généré.");
            }
            return;
        }

        const { employee, vehicle } = selected[done];
        frappe.call({
            method: "customization_app.generer_bl.generer_bl_employe",
            args: { date, employee, vehicle },
            callback: function(r) {
                done++;
                frappe.show_progress("Génération des BL...", done, selected.length);

                if (r.message) {
                    const { dns_reels, dns_virtuels, employee_name } = r.message;
                    (dns_reels || []).forEach(dn => allDns.push({ name: dn, virtual: false, employee_name }));
                    (dns_virtuels || []).forEach(dn => allDns.push({ name: dn, virtual: true, employee_name }));
                }
                processNext();
            },
            error: function() {
                done++;
                frappe.show_progress("Génération des BL...", done, selected.length);
                processNext();
            },
        });
    }

    processNext();
}

function _print_all_dns(dns) {
    if (!dns.length) return;

    // Ouvrir chaque DN dans un nouvel onglet pour impression (format Aqua World BL)
    frappe.msgprint({
        title: "BLs générés",
        message: `${dns.length} bon(s) de livraison prêts. Ouverture en cours...`,
        indicator: "green",
    });

    // Petit délai pour que le message soit visible
    setTimeout(() => {
        dns.forEach((dn, i) => {
            setTimeout(() => {
                const url = `/printview?doctype=Delivery%20Note&name=${encodeURIComponent(dn.name)}&format=Aqua%20World%20BL&lang=fr&trigger_print=1`;
                window.open(url, `_bl_${i}`);
            }, i * 400);
        });
    }, 600);
}


