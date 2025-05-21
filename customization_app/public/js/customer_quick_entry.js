frappe.provide("frappe.ui.form");


frappe.ui.form.CustomerQuickEntryForm = class CustomerQuickEntryForm extends (
    frappe.ui.form.QuickEntryForm
){
	constructor(doctype, after_insert, init_callback, doc, force) {
		super(doctype, after_insert, init_callback, doc, force);
		this.skip_redirect_on_error = true;

		this.villes_par_gouvernorat = {'Ariana': {'Ariana ville': 'Secteur 2',
			'Borj Louzir': 'Secteur 2',
			'Borj touil': 'Secteur 2',
			'Charguia 1': 'Secteur 2',
			'Charguia 2': 'Secteur 2',
			'Chotrana 1': 'Secteur 2',
			'Chotrana 2': 'Secteur 1',
			'Chotrana 3': 'Secteur 1',
			'Cité Ennasr 1': 'Secteur 2',
			'Cité Ennasr 2': 'Secteur 2',
			'Dar Fadhal': 'Secteur 1',
			'El Ghazala': 'Secteur 2',
			'El Menzah 5': 'Secteur 2',
			'El Menzah 6': 'Secteur 2',
			'El Menzah 7': 'Secteur 2',
			'El Menzah 8': 'Secteur 2',
			'Ettadhamen': 'Secteur 3',
			'Kalaat El Andalous': 'Secteur 8',
			'Mnihla': 'Secteur 3',
			'Raoued': 'Secteur 1',
			'Riadh Andalous': 'Secteur 2',
			'Sidi Thabet': 'Secteur 3',
			'Soukra': 'Secteur 1'},
		   'Beja': {'Amdoun': 'Hors Secteur',
			'Beja': 'Hors Secteur',
			'Goubellat': 'Hors Secteur',
			'Majaz al Bab': 'Hors Secteur',
			'Nefza': 'Hors Secteur',
			'Teboursouk': 'Hors Secteur',
			'Testour': 'Hors Secteur',
			'Thibar': 'Hors Secteur'},
		   'Ben Arous': {'Ben Arous': 'Secteur 5',
			'Borj Cédria': 'Secteur 6',
			'BouMhel el-Bassatine': 'Secteur 6',
			"EL M'GHIRA": 'Secteur 5',
			'El Mourouj': 'Secteur 5',
			'Ezzahra': 'Secteur 6',
			'Fouchana': 'Secteur 5',
			'Hammam Chott': 'Secteur 6',
			'Hammam Lif': 'Secteur 6',
			'Medina Jedida': 'Secteur 5',
			'Megrine': 'Secteur 5',
			'Mohamedia': 'Secteur 5',
			'Mornag': 'Secteur 5',
			'Naassen': 'Secteur 5',
			'Rades': 'Secteur 6'},
		   'Bizerte': {'Bizerte': 'Secteur 8',
			'Djoumime': 'Hors Secteur',
			'El Alia': 'Secteur 8',
			'Ghar El Melh': 'Secteur 8',
			'Ghezala': 'Hors Secteur',
			'Mateur': 'Hors Secteur',
			'Menzel Bourguiba': 'Secteur 8',
			'Menzel Jemil': 'Secteur 8',
			'Ras Jebel': 'Secteur 8',
			'Sejenane': 'Hors Secteur',
			'Tinja': 'Secteur 8',
			'Utica': 'Secteur 8',
			'Zarzouna': 'Secteur 8'},
		   'Gabès': {'Gabes': 'Hors Secteur',
			'Ghannouch': 'Hors Secteur',
			'Hamma': 'Hors Secteur',
			'Mareth': 'Hors Secteur',
			'Matmata': 'Hors Secteur',
			'Menzel Habib': 'Hors Secteur',
			'Metouia': 'Hors Secteur'},
		   'Gafsa': {'Belkhir': 'Hors Secteur',
			'Gafsa': 'Hors Secteur',
			'Guetar': 'Hors Secteur',
			'Ksar': 'Hors Secteur',
			'Mdhila': 'Hors Secteur',
			'Metlaoui': 'Hors Secteur',
			'Oum Larais': 'Hors Secteur',
			'Redeyef': 'Hors Secteur',
			'Sened': 'Hors Secteur',
			'Sidi Aich': 'Hors Secteur'},
		   'Jendouba': {'Ain Draham': 'Hors Secteur',
			'Balta': 'Hors Secteur',
			'Bou Salem': 'Hors Secteur',
			'Fernana': 'Hors Secteur',
			'Ghardimaou': 'Hors Secteur',
			'Jendouba': 'Hors Secteur',
			'Oued Melliz': 'Hors Secteur',
			'Tabarka': 'Hors Secteur'},
		   'Kairouan': {'Alaâ': 'Hors Secteur',
			'Bouhajla': 'Hors Secteur',
			'Chebika': 'Hors Secteur',
			'Echrarda': 'Hors Secteur',
			'Haffouz': 'Hors Secteur',
			'Hajeb El Ayoun': 'Hors Secteur',
			'Kairouan': 'Hors Secteur',
			'Nasrallah': 'Hors Secteur',
			'Oueslatia': 'Hors Secteur',
			'Sbikha': 'Hors Secteur'},
		   'Kasserine': {'Ayoun': 'Hors Secteur',
			'Ezzouhour': 'Hors Secteur',
			'Feriana': 'Hors Secteur',
			'Foussana': 'Hors Secteur',
			'Hassi El Ferid': 'Hors Secteur',
			'Hidra': 'Hors Secteur',
			'Jedeliane': 'Hors Secteur',
			'Kasserine': 'Hors Secteur',
			'Magel Bel Abbes': 'Hors Secteur',
			'Sbeitla': 'Hors Secteur',
			'Sbiba': 'Hors Secteur',
			'Thala': 'Hors Secteur'},
		   'Kebili': {'Douz': 'Hors Secteur',
			'Faouar': 'Hors Secteur',
			'Kebili': 'Hors Secteur',
			'Souk El Ahed': 'Hors Secteur'},
		   'Kef': {'Dahmani': 'Hors Secteur',
			'Es Sers': 'Hors Secteur',
			'Jerissa': 'Hors Secteur',
			'Kalaa Khasbat': 'Hors Secteur',
			'Kalaat Senane': 'Hors Secteur',
			'Kef East': 'Hors Secteur',
			'Kef West': 'Hors Secteur',
			'Ksour': 'Hors Secteur',
			'Nebeur': 'Hors Secteur',
			'Sakiet Sidi Youssef': 'Hors Secteur',
			'Tajerouine': 'Hors Secteur',
			'Touiref': 'Hors Secteur'},
		   'Mahdia': {'Boumerdes': 'Hors Secteur',
			'Chebba': 'Hors Secteur',
			'Chorbane': 'Hors Secteur',
			'El Jam': 'Hors Secteur',
			'Hbira': 'Hors Secteur',
			'Ksour Essef': 'Hors Secteur',
			'Mahdia': 'Hors Secteur',
			'Melloulech': 'Hors Secteur',
			'Ouled Chamekh': 'Hors Secteur',
			'Sidi Alouane': 'Hors Secteur',
			'Souassi': 'Hors Secteur'},
		   'Manouba': {'Borj El Amri': 'Secteur 7',
			'Den Den': 'Secteur 3',
			'Douar Hicher': 'Secteur 3',
			'El Battan': 'Secteur 7',
			'Jedaida': 'Secteur 7',
			'Manouba': 'Secteur 3',
			'Mornaguia': 'Secteur 7',
			'Oued Ellil': 'Secteur 3',
			'Tebourba': 'Secteur 7',
			'Zahrouni': 'Secteur 4'},
		   'Medenine': {'Ben Gardane': 'Hors Secteur',
			'Beni Khedache': 'Hors Secteur',
			'Djerba Ajim': 'Hors Secteur',
			'Djerba Houmt Souk': 'Hors Secteur',
			'Djerba Midoun': 'Hors Secteur',
			'Medenine': 'Hors Secteur',
			'Sidi Makhlouf': 'Hors Secteur',
			'Zarzis': 'Hors Secteur'},
		   'Monastir': {'Bekalta': 'Hors Secteur',
			'Bembla': 'Hors Secteur',
			'Beni Hassen': 'Hors Secteur',
			'Jammel': 'Hors Secteur',
			'Ksar Hellal': 'Hors Secteur',
			'Ksibet El Mediouni': 'Hors Secteur',
			'Moknine': 'Hors Secteur',
			'Monastir': 'Hors Secteur',
			'Ouerdanine': 'Hors Secteur',
			'Sahline': 'Hors Secteur',
			'Sayada-Lamta-Bou Hjar': 'Hors Secteur',
			'Teboulba': 'Hors Secteur',
			'Zeramdine': 'Hors Secteur'},
		   'Nabeul': {'Beni Khalled': 'Secteur 9',
			'Beni Khiar': 'Secteur 9',
			'Bou Argoub': 'Secteur 9',
			'Dar Chaabane El Fehri': 'Secteur 9',
			'El Mida': 'Hors Secteur',
			'Grombalia': 'Secteur 9',
			'Hammam Ghezaz': 'Hors Secteur',
			'Hammamet': 'Secteur 9',
			'Haouaria': 'Hors Secteur',
			'Kelibia': 'Hors Secteur',
			'Korba': 'Secteur 9',
			'Menzel Bouzelfa': 'Secteur 9',
			'Menzel Temime': 'Hors Secteur',
			'Nabeul': 'Secteur 9',
			'Soliman': 'Secteur 6',
			'Takelsa': 'Hors Secteur',
			'Tazarka': 'Secteur 9'},
		   'Sfax': {'Agareb': 'Hors Secteur',
			'Bir Ali Ben Khelifa': 'Hors Secteur',
			'El Amra': 'Hors Secteur',
			'El Ghraiba': 'Hors Secteur',
			'Hencha': 'Hors Secteur',
			'Jebeniana': 'Hors Secteur',
			'Kerkennah': 'Hors Secteur',
			'Mahres': 'Hors Secteur',
			'Menzel Chaker': 'Hors Secteur',
			'Sakiet Eddaier': 'Hors Secteur',
			'Sakiet Ezzit': 'Hors Secteur',
			'Sfax Ville': 'Hors Secteur',
			'Skhira': 'Hors Secteur',
			'Thyna': 'Hors Secteur'},
		   'Sidi Bouzid': {'Bir El Hfay': 'Hors Secteur',
			'Jelma': 'Hors Secteur',
			'Mazzouna': 'Hors Secteur',
			'Meknassi': 'Hors Secteur',
			'Menzel Bouzaiene': 'Hors Secteur',
			'Ouled Haffouz': 'Hors Secteur',
			'Regueb': 'Hors Secteur',
			'Sabalat Ouled Asker': 'Hors Secteur',
			'Sidi Ali Ben Aoun': 'Hors Secteur',
			'Sidi Bouzid': 'Hors Secteur',
			'Souk Jedid': 'Hors Secteur'},
		   'Siliana': {'Bargou': 'Hors Secteur',
			'Bouarada': 'Hors Secteur',
			'El Aroussa': 'Hors Secteur',
			'El Krib': 'Hors Secteur',
			'Gaafour': 'Hors Secteur',
			'Kesra': 'Hors Secteur',
			'Makthar': 'Hors Secteur',
			'Rouhia': 'Hors Secteur',
			'Sidi Bourouis': 'Hors Secteur',
			'Siliana': 'Hors Secteur'},
		   'Sousse': {'Akouda': 'Hors Secteur',
			'Bouficha': 'Hors Secteur',
			'Enfidha': 'Hors Secteur',
			'Hammam Sousse': 'Hors Secteur',
			'Hergla': 'Hors Secteur',
			'Kalaa Kebira': 'Hors Secteur',
			'Kalaa Sghira': 'Hors Secteur',
			'Kondar': 'Hors Secteur',
			"M'Saken": 'Hors Secteur',
			'Sidi Bou Ali': 'Hors Secteur',
			'Sidi El Heni': 'Hors Secteur',
			'Sousse Jaouhara': 'Hors Secteur',
			'Sousse Medina': 'Hors Secteur',
			'Sousse Riadh': 'Hors Secteur',
			'Sousse Sidi Abdelhamid': 'Hors Secteur',
			'Zaouiet Ksibet Thrayet': 'Hors Secteur'},
		   'Tataouine': {'Bir Lahmar': 'Hors Secteur',
			'Dhiba': 'Hors Secteur',
			'Ghomrassen': 'Hors Secteur',
			'Remada': 'Hors Secteur',
			'Samar': 'Hors Secteur',
			'Tataouine': 'Hors Secteur'},
		   'Tozeur': {'Degueche': 'Hors Secteur',
			'Hazoua': 'Hors Secteur',
			'Nefta': 'Hors Secteur',
			'Tamaghza': 'Hors Secteur',
			'Tozeur': 'Hors Secteur'},
		   'Tunis': {'Ain Zaghouan': 'Secteur 1',
			'Aouina': 'Secteur 1',
			'Bab Bhar': 'Secteur 4',
			'Bab Souika': 'Secteur 4',
			'Carthage': 'Secteur 1',
			'Centre urbain nord': 'Secteur 2',
			'Cité El Khadra': 'Secteur 2',
			'El Agba': 'Secteur 3',
			'El Hrairia': 'Secteur 4',
			'El Kabaria': 'Secteur 5',
			'El Manar 2': 'Secteur 2',
			'El Menzah': 'Secteur 2',
			'El Menzah 1': 'Secteur 2',
			'El Menzah 2': 'Secteur 2',
			'El Menzah 3': 'Secteur 2',
			'El Menzah 4': 'Secteur 2',
			'El Menzah 9': 'Secteur 2',
			'El Omrane': 'Secteur 4',
			'El Omrane Supérieur': 'Secteur 3',
			'El Ouardia': 'Secteur 5',
			'Ettahrir': 'Secteur 3',
			'Ezzouhour': 'Secteur 4',
			'Gammarth': 'Secteur 1',
			'Jardin de carthage': 'Secteur 1',
			'Jebel Jelloud': 'Secteur 5',
			'La Goulette': 'Secteur 1',
			'La Marsa': 'Secteur 1',
			'Lac 1': 'Secteur 2',
			'Lac 2': 'Secteur 1',
			'Le Bardo': 'Secteur 3',
			'Le Kram': 'Secteur 1',
			'Medina': 'Secteur 4',
			'Mutuelle Ville': 'Secteur 2',
			'Sidi Bou Said': 'Secteur 1',
			'Sidi El Béchir': 'Secteur 5',
			'Sidi Hassine': 'Secteur 4',
			'Sijoumi': 'Secteur 4',
			'Tunis': 'Secteur 4'},
		   'Zaghouan': {'Bir Mchergua': 'Hors Secteur',
			'Fahs': 'Hors Secteur',
			'Nadhour': 'Hors Secteur',
			'Saouaf': 'Hors Secteur',
			'Zaghouan': 'Hors Secteur',
			'Zriba': 'Hors Secteur'}};

	}

	async render_dialog() {
		this.mandatory = this.mandatory.concat(await this.get_variant_fields());

		await super.render_dialog();

		// Add event handlers
		    // Initially hide the two fields
		// setTimeout(() => {
			// this.dialog.set_df_property('custom_envois_automatique_de_la_bl', 'hidden', 1);
			// this.dialog.set_df_property('custom_generation_facture_mensuelle', 'hidden', 1);
		this.dialog.set_value('facturation_mensuelle', 'Non');
		this.dialog.set_value('envois_bl', 'Non');

// Met à jour la visibilité selon le groupe client actuel
		this.set_fields_based_on_customer_group();

		// 	// this.dialog.get_field('custom_generation_facture_mensuelle').set_df_property('hidden', 1);
		// 	// this.dialog.get_field('custom_envois_automatique_de_la_bl').set_df_property('hidden', 1);
		// }, 200);
		// 		// Initialiser les valeurs par défaut des selects cachés
		// this.dialog.set_value('facturation_mensuelle', 'Non');
		// this.dialog.set_value('envois_bl', 'Non');
		this.dialog.refresh();
		this.dialog.fields_dict.country.df.onchange = () => {
			this.on_country_change();
		};
		this.dialog.fields_dict.custom_state_s.df.onchange = () => {
			this.on_governorate_change();
		};
		this.dialog.fields_dict.custom_villes_s.df.onchange = () => {
			this.on_city_change();
		};
		this.dialog.fields_dict.customer_group.df.onchange = () => {
			this.set_fields_based_on_customer_group();
		};
	}
	async insert() {

		const mobile1 = (this.dialog.get_value("mobile_number1") || "").trim();
		const mobile2 = (this.dialog.get_value("mobile_number2") || "").trim();
		const country = this.dialog.get_value("country");
	
		const mobile1_field = this.dialog.fields_dict.mobile_number1;
		const mobile2_field = this.dialog.fields_dict.mobile_number2;
		
		if (country === "Tunisia") {
			const isValid = num => /^\d{8}$/.test(num);
			let has_error = false;
		
			// Validation Mobile 1
			if (!isValid(mobile1)) {
				mobile1_field.$wrapper.addClass("has-error");
				frappe.msgprint("📵 Numéro de mobile 1 doit comporter exactement 8 chiffres pour la Tunisie.");
				has_error = true;
			}
		
			// Validation Mobile 2
			if (mobile2 && !isValid(mobile2)) {
				mobile2_field.$wrapper.addClass("has-error");
				frappe.msgprint("📵 Numéro de mobile 2 doit comporter exactement 8 chiffres pour la Tunisie.");
				has_error = true;
			}
		
			// Remove error on typing
			[mobile1_field, mobile2_field].forEach(field => {
				field.$wrapper.find("input").off("input").on("input", function () {
					$(this).closest(".frappe-control").removeClass("has-error");
				});
			});
		
			// Stop if there's an error
			if (has_error) {
				this.dialog.set_primary_action(__("Sauvegarder"), () => this.insert());
				return;
			}
		}
		// Appel backend pour vérifier les doublons
		const { message: duplicates } = await frappe.call({
			method: "customization_app.customization.check_duplicate_phone",
			args: {
				mobile1: mobile1,
				mobile2: mobile2
			}
		});
	
		// Si doublons trouvés
		if (duplicates && duplicates.length > 0) {
			// Construire liste HTML avec boutons vers les clients existants
			const html_customers = duplicates.map((c, i) => {
				return `
					<div style="margin-bottom: 10px; padding: 8px; border: 1px solid #ddd; border-radius: 6px;">
						<b>${c.customer_name}</b><br>
						<span style="color: gray;">${c.custom_liste_telephone || ""}</span><br>
						<button class="btn btn-sm btn-secondary open-existing" data-index="${i}">
							📋 Ouvrir ce client
						</button>
					</div>
				`;
			}).join("");
	
			const dialog = new frappe.ui.Dialog({
				title: __("Un client avec ce numéro existe déjà"),
				fields: [
					{
						fieldtype: "HTML",
						fieldname: "duplicate_list",
						options: `
							<div>${html_customers}</div>
							<hr>
							<div style="text-align: right;">
								<button class="btn btn-primary btn-create-anyway">
									➕ Créer quand même
								</button>
							</div>
						`
					}
				],
				primary_action_label: __("Annuler"),
				primary_action: () => {
					dialog.hide();
					// Resolve the promise with cancel action
					if (choiceResolve) choiceResolve({ action: "cancel" });
				}
			});
	
			dialog.show();
	
			let choiceResolve;
			const choice = await new Promise(resolve => {
				choiceResolve = resolve;
				dialog.$wrapper.find(".open-existing").on("click", function () {
					const index = $(this).data("index");
					dialog.hide();
					resolve({ action: "use_existing", customer: duplicates[index] });
				});
				dialog.$wrapper.find(".btn-create-anyway").on("click", function () {
					dialog.hide();
					resolve({ action: "create_new" });
				});
				// No need for additional cancel button handler since primary_action handles it
			});
	
			if (choice.action === "cancel") {
				// User clicked "Annuler" => do nothing, just stop insertion
				this.dialog.set_primary_action(__("Sauvegarder"), () => this.insert());
				return;
			}
	
			if (choice.action === "use_existing") {
				const customer = await frappe.db.get_doc("Customer", choice.customer.name);
	
				if (this.after_insert && typeof this.after_insert === "function") {
					this.after_insert(customer);
				}
	
				this.dialog.hide();
				return;
			}
			// else: continue to create new client
		}
	
		// Mapping email et numéro
		const map_field_names = {
			email_address: "email_id",
			mobile_number: "mobile_no",
		};
	
		Object.entries(map_field_names).forEach(([fieldname, new_fieldname]) => {
			this.dialog.doc[new_fieldname] = this.dialog.doc[fieldname];
			delete this.dialog.doc[fieldname];
		});
	
		return super.insert();
	}
	// insert() {

	// 	const map_field_names = {
	// 		email_address: "email_id",
	// 		mobile_number: "mobile_no",
	// 	};

	// 	Object.entries(map_field_names).forEach(([fieldname, new_fieldname]) => {
	// 		this.dialog.doc[new_fieldname] = this.dialog.doc[fieldname];
	// 		delete this.dialog.doc[fieldname];
	// 	});

	// 	return super.insert();
	// }
	async get_variant_fields() {
		await frappe.model.with_doctype('Address');
        const address_meta = frappe.get_meta('Address');

		        // Pick only the fields you care about
		const address_fields = address_meta.fields.filter(f =>
			['address_line1', 'address_line2', 'city', 'state', 'pincode', 'country'].includes(f.fieldname)
		);
		let variant_fields = [
			{
				label: __("Generation facture mensuelle"),
				fieldname: "facturation_mensuelle",
				fieldtype: "Select",
				options: ["Oui", "Non"].join("\n"),
				default: "Non",
				hidden: 0,  // caché par défaut
			},
			{
				label: __("Envois automatique de la BL"),
				fieldname: "envois_bl",
				fieldtype: "Select",
				options: ["Oui", "Non"].join("\n"),
				default: "Non",
				hidden: 0,  // caché par défaut
			},
            {
                fieldtype: "Section Break",
                label: __("Primary Contact Details"),
                collapsible: 1,
            },
			{
                label: __("Nom du Contact"),
                fieldname: "nom contact",
                fieldtype: "Data"
            },
			
            {
                label: __("Email Id"),
                fieldname: "email_address",
                fieldtype: "Data",
                options: "Email",
            },
            {
                fieldtype: "Column Break",
            },
            {
                label: __("Mobile Number 1"),
                fieldname: "mobile_number1",
                fieldtype: "Phone",
				options: "Phone",
				reqd: 1,
            },
			{
                label: __("Mobile Number 2"),
                fieldname: "mobile_number2",
                fieldtype: "Phone",
				options: "Phone",
            },
            {
                fieldtype: "Section Break",
                label: __("Primary Address Details"),
                collapsible: 1,
            },
            {
                label: __("Country"),
                fieldname: "country",
                fieldtype: "Link",
                options: "Country",
				reqd: 1 
            },
			{
                label: __("Address"),
                fieldname: "address_line1",
                fieldtype: "Data",
				reqd: 1,
            },
			{
				label: __("Secteur"),
				fieldname: "custom_secteur",
				fieldtype: "Link",
				options: "Secteur geographique"
			},
			{
                fieldtype: "Column Break",
            },

			{
                label: __("State"),
                fieldname: "custom_state_s",
                fieldtype: "Select",
                depends_on: "eval:doc.country == 'Tunisia'"
            },
            {
                label: __("State"),
                fieldname: "custom_state_d",
                fieldtype: "Data",
                depends_on: "eval:doc.country != 'Tunisia'"
            },
			{
                label: __("City"),
                fieldname: "custom_villes_s",
                fieldtype: "Select",
                depends_on: "eval:doc.country == 'Tunisia'"
            },
			{
                label: __("City"),
                fieldname: "custom_villes_d",
                fieldtype: "Data",
                depends_on: "eval:doc.country != 'Tunisia'"
            },


			{
                label: __("Postal Code"),
                fieldname: "pincode",
                fieldtype: "Data"
            }

        ];

		return variant_fields;
	}
	on_country_change() {
        const country = this.dialog.get_value('country');

        if (country === 'Tunisia') {
            const states = Object.keys(this.villes_par_gouvernorat);
            this.dialog.set_df_property('custom_state_s', 'options', states);
			this.dialog.set_df_property('custom_state_s', 'reqd', 1);
			this.dialog.set_df_property('custom_villes_s', 'reqd', 1);
			this.dialog.set_df_property('custom_state_d', 'reqd', 0);
			this.dialog.set_df_property('custom_villes_d', 'reqd', 0);
            this.dialog.set_value('custom_secteur', '');
        } else {
            this.dialog.set_value('custom_secteur', 'Hors Secteur');
            this.dialog.set_value('custom_state_s', '');
            this.dialog.set_value('custom_villes_s', '');
			this.dialog.set_df_property('custom_state_s', 'reqd', 0);
			this.dialog.set_df_property('custom_villes_s', 'reqd', 0);
			this.dialog.set_df_property('custom_state_d', 'reqd', 1);
			this.dialog.set_df_property('custom_villes_d', 'reqd', 1);
        }

        this.dialog.refresh();
    }

    on_governorate_change() {
        const state = this.dialog.get_value('custom_state_s');
        if (state in this.villes_par_gouvernorat) {
            const villes = Object.keys(this.villes_par_gouvernorat[state]);
            this.dialog.set_df_property('custom_villes_s', 'options', villes);
        } else {
            this.dialog.set_df_property('custom_villes_s', 'options', []);
        }
        this.dialog.set_value('custom_villes_s', '');
        this.dialog.refresh();
    }

    on_city_change() {
        const state = this.dialog.get_value('custom_state_s');
        const city = this.dialog.get_value('custom_villes_s');

        if (state && city && this.villes_par_gouvernorat[state] && this.villes_par_gouvernorat[state][city]) {
            const secteur = this.villes_par_gouvernorat[state][city];
            this.dialog.set_value('custom_secteur', secteur);
        } else {
            this.dialog.set_value('custom_secteur', '');
        }
        this.dialog.refresh();
    }
	set_fields_based_on_customer_group() {
		    const customer_group = this.dialog.get_value('customer_group');
			const allowed_groups = ["Technicien", "Quincaillerie", "Compte Pro"];
			// const facturation_mensuelle = ["Quincaillerie", "Compte Pro"];

			// Champs concernés
			const fields = [
				'facturation_mensuelle',
				'envois_bl'
			];
			this.dialog.set_df_property('custom_generation_facture_mensuelle', 'hidden', 1);
			this.dialog.set_df_property('custom_envois_automatique_de_la_bl', 'hidden', 1);
			// const show_facturation = facturation_mensuelle.includes(customer_group);
			const show_allowed = allowed_groups.includes(customer_group);
			this.dialog.set_value('custom_envoi_sms', "Oui");
			// Gérer visibilité et readonly des champs facturation/envois BL
			fields.forEach(field => {
				this.dialog.set_df_property(field, 'hidden', allowed_groups ? 0 : 1);
				this.dialog.set_df_property(field, 'read_only', allowed_groups ? 0 : 1);
			});

			if (show_allowed) {
				// Valeurs par défaut pour groupes autorisés
				this.dialog.set_value('custom_intéressé_par_le_service_entretien', "Non");

				// Propriétés spécifiques
				this.dialog.set_df_property('custom_intéressé_par_le_service_entretien', 'read_only', 1);
				this.dialog.set_df_property('email_address', 'reqd', 1);
                if (customer_group === "Technicien") {
					this.dialog.set_value('facturation_mensuelle', "Non");
					this.dialog.set_df_property('facturation_mensuelle', 'hidden', 0);
					this.dialog.set_df_property('facturation_mensuelle', 'read_only', 1);
				}

			} else {
				// Pour les autres groupes
				this.dialog.set_value('custom_intéressé_par_le_service_entretien', "Oui");


				this.dialog.set_df_property('custom_intéressé_par_le_service_entretien', 'read_only', 0);
				this.dialog.set_df_property('email_address', 'reqd', 0);

				// Cacher et mettre readonly sur facturation/envois BL si hors facturation_mensuelle
				fields.forEach(field => {
					this.dialog.set_df_property(field, 'hidden', 1);
					this.dialog.set_df_property(field, 'read_only', 1);
					this.dialog.set_value(field, "Non");
				});
			}


			this.dialog.refresh();
	}
};
