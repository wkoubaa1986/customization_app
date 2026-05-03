// Auto-select customer in POS when opened from Mes Interventions Employe
// Also injects payment-modes vertical layout CSS (must run after erpnext.bundle.css)
(function () {
    // Inject payment modes CSS once — runs after all bundles so it wins specificity by order
    (function inject_payment_css() {
        if (document.getElementById("cust-pos-pay-css")) return;
        const s = document.createElement("style");
        s.id = "cust-pos-pay-css";
        s.textContent = `
            /* ── vertical stack ── */
            .point-of-sale-app > .payment-container > .payment-modes {
                flex-direction: column !important;
                overflow-x: hidden !important;
                overflow-y: auto !important;
                flex-shrink: 0 !important;
                gap: 8px !important;
            }
            .point-of-sale-app > .payment-container > .payment-modes > .payment-mode-wrapper {
                min-width: 100% !important;
                width: 100% !important;
                padding: 0 !important;
            }

            /* ── base card ── */
            .point-of-sale-app > .payment-container > .payment-modes > .payment-mode-wrapper > .mode-of-payment {
                display: flex !important;
                flex-wrap: wrap !important;
                align-items: center !important;
                padding: 14px 18px !important;
                font-size: 16px !important;
                font-weight: 700 !important;
                border: 2px solid #e5e7eb !important;
                border-radius: 10px !important;
                min-height: unset !important;
                cursor: pointer !important;
                transition: background 0.15s, border-color 0.15s !important;
            }

            /* ── icon via ::before ── */
            .point-of-sale-app > .payment-container > .payment-modes > .payment-mode-wrapper > .mode-of-payment[data-mode^="esp"]::before  { content: "💵"; font-size: 22px; margin-right: 10px; }
            .point-of-sale-app > .payment-container > .payment-modes > .payment-mode-wrapper > .mode-of-payment[data-mode^="ch"]::before   { content: "📄"; font-size: 22px; margin-right: 10px; }
            .point-of-sale-app > .payment-container > .payment-modes > .payment-mode-wrapper > .mode-of-payment[data-mode^="dette"]::before { content: "⚠️"; font-size: 22px; margin-right: 10px; }

            /* ── idle colors ── */
            .point-of-sale-app > .payment-container > .payment-modes > .payment-mode-wrapper > .mode-of-payment[data-mode^="esp"]   { background: #d1fae5 !important; border-color: #6ee7b7 !important; }
            .point-of-sale-app > .payment-container > .payment-modes > .payment-mode-wrapper > .mode-of-payment[data-mode^="ch"]    { background: #ede9fe !important; border-color: #c4b5fd !important; }
            .point-of-sale-app > .payment-container > .payment-modes > .payment-mode-wrapper > .mode-of-payment[data-mode^="dette"] { background: #fee2e2 !important; border-color: #fca5a5 !important; }

            /* ── selected = blue ── */
            .point-of-sale-app > .payment-container > .payment-modes > .payment-mode-wrapper > .mode-of-payment[data-mode^="esp"].border-primary,
            .point-of-sale-app > .payment-container > .payment-modes > .payment-mode-wrapper > .mode-of-payment[data-mode^="ch"].border-primary,
            .point-of-sale-app > .payment-container > .payment-modes > .payment-mode-wrapper > .mode-of-payment[data-mode^="dette"].border-primary {
                background: #1d4ed8 !important;
                border-color: #1e40af !important;
                color: #ffffff !important;
            }

            /* ── amount label floated right ── */
            .point-of-sale-app > .payment-container > .payment-modes > .payment-mode-wrapper > .mode-of-payment > .pay-amount {
                margin-left: auto !important;
                float: none !important;
                font-size: 14px !important;
                font-weight: 600 !important;
            }

            /* ── input on new line (flex-basis 100%) ── */
            .point-of-sale-app > .payment-container > .payment-modes > .payment-mode-wrapper > .mode-of-payment > .mode-of-payment-control {
                display: none !important;
                flex-basis: 100% !important;
                margin-top: 10px !important;
            }
            .point-of-sale-app > .payment-container > .payment-modes > .payment-mode-wrapper > .mode-of-payment.border-primary > .mode-of-payment-control {
                display: block !important;
            }

            /* ── cash shortcuts on new line ── */
            .point-of-sale-app > .payment-container > .payment-modes > .payment-mode-wrapper > .mode-of-payment > .cash-shortcuts {
                display: none !important;
                flex-basis: 100% !important;
                grid-template-columns: repeat(3,1fr);
                gap: 6px;
                margin-top: 8px;
                font-size: 13px;
                text-align: center;
            }
            .point-of-sale-app > .payment-container > .payment-modes > .payment-mode-wrapper > .mode-of-payment.border-primary > .cash-shortcuts {
                display: grid !important;
            }
            .point-of-sale-app > .payment-container > .payment-modes > .payment-mode-wrapper > .mode-of-payment.border-primary > .cash-shortcuts > .shortcut {
                background: rgba(255,255,255,0.2);
                border-radius: 6px;
                padding: 4px 6px;
                cursor: pointer;
                color: #fff;
                font-weight: 500;
            }

            /* ── hide Total net and TVA rows in cart ── */
            .point-of-sale-app > .customer-cart-container > .cart-container > .abs-cart-container > .cart-totals-section > .net-total-container,
            .point-of-sale-app > .customer-cart-container > .cart-container > .abs-cart-container > .cart-totals-section > .taxes-container {
                display: none !important;
            }

            /* ── reduce gap between items and "Ajouter une promotion" ── */
            .point-of-sale-app > .customer-cart-container > .cart-container > .abs-cart-container > .cart-items-section {
                flex: 0 1 auto !important;
                overflow-y: auto !important;
                max-height: 55vh !important;
            }
            .point-of-sale-app > .customer-cart-container > .cart-container > .abs-cart-container > .cart-totals-section {
                margin-top: 4px !important;
            }

            /* ── reduce gap between "Information additionnelle" and totals ── */
            .point-of-sale-app > .payment-container > .fields-numpad-container > .fields-section {
                padding-bottom: 4px !important;
            }
            .point-of-sale-app > .payment-container > .totals-section {
                padding-top: 4px !important;
                margin-top: 0 !important;
            }
        `;
        document.head.appendChild(s);
    })();

    const customer = localStorage.getItem("pos_customer");
    const from_intervention = localStorage.getItem("pos_from_intervention");

    if (!from_intervention || !customer) return;

    localStorage.removeItem("pos_from_intervention");
    localStorage.removeItem("pos_customer");

    let attempts = 0;

    const interval = setInterval(function () {
        attempts++;
        if (attempts > 120) { clearInterval(interval); return; }

        const pos = window.cur_pos;
        if (!pos) return;

        const cart = pos.cart;
        if (!cart || !cart.customer_field || !cart.customer_field.$input) return;

        // Wait until the POS Invoice document is created (frm.doc.name exists)
        // — before this point, the cart resets on init and clears any value we set
        const frm = pos.frm;
        if (!frm || !frm.doc || !frm.doc.name) return;

        clearInterval(interval);

        // Clear the item search cache so stale results don't persist across sessions
        if (pos.item_selector) {
            pos.item_selector.search_index = {};
        }

        // Hide item group selector — expand search bar to fill full width
        const style = document.createElement("style");
        style.textContent = `
            .item-group-field { display: none !important; }
            .filter-section { display: block !important; }
            .search-field { width: 100% !important; margin: 0 !important; }
            .search-field .form-control { font-size: 16px !important; padding: 10px 14px !important; height: 46px !important; width: 100% !important; box-sizing: border-box !important; }
        `;
        document.head.appendChild(style);

        // set_value is the proper Frappe Link API — triggers full POS onchange chain:
        // model.set_value → script_manager.trigger → fetch_customer_details → unfreeze
        cart.customer_field.set_value(customer);

    }, 500);
})();
