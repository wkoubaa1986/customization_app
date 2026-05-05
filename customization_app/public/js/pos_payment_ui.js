/* POS Payment UI Override — rewritten for correct bootstrap timing */
(function () {
    var MODE_CONFIG = [
        { match: /esp[eè]/i,   icon: "💵", bg_idle: "#d1fae5", border_idle: "#6ee7b7" },
        { match: /ch[eè]que/i, icon: "📄", bg_idle: "#ede9fe", border_idle: "#c4b5fd" },
        { match: /dette/i,     icon: "⚠️",  bg_idle: "#fee2e2", border_idle: "#fca5a5" }
    ];
    var BG_SEL = "#1d4ed8";

    function get_config(label) {
        for (const c of MODE_CONFIG) {
            if (c.match.test(label)) return c;
        }
        return null;
    }

    // ── Inject CSS once ─────────────────────────────────────────────────────
    function inject_styles() {
        if (document.getElementById("pos-payment-ui-style")) return;
        const style = document.createElement("style");
        style.id = "pos-payment-ui-style";
        style.textContent = `
/* Reset horizontal scroll → vertical stack */
.point-of-sale-app > .payment-container > .payment-modes {
    flex-direction: column !important;
    overflow-x: hidden !important;
    overflow-y: auto !important;
    gap: 8px !important;
    padding-bottom: 8px !important;
}
.point-of-sale-app > .payment-container > .payment-modes > .payment-mode-wrapper {
    min-width: 100% !important;
    width: 100% !important;
    padding: 0 !important;
}
/* Base card */
.point-of-sale-app .mode-of-payment.pos-custom-card {
    display: flex !important;
    flex-direction: column !important;
    align-items: stretch !important;
    padding: 0 !important;
    border-radius: 10px !important;
    border: 2px solid transparent !important;
    overflow: hidden !important;
    transition: border-color 0.15s, box-shadow 0.15s !important;
    cursor: pointer !important;
    min-height: unset !important;
}
/* Card title row */
.pos-custom-card .pos-card-title {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 14px 18px;
    font-size: 16px;
    font-weight: 700;
    color: #1e293b;
    user-select: none;
}
.pos-custom-card .pos-card-icon {
    font-size: 22px;
    line-height: 1;
}
.pos-custom-card .pos-card-label {
    flex: 1;
}
.pos-custom-card .pos-card-amount {
    font-size: 14px;
    font-weight: 600;
    color: #374151;
}
/* Selected state */
.pos-custom-card.border-primary {
    border-color: #1d4ed8 !important;
    box-shadow: 0 0 0 3px rgba(29,78,216,0.18) !important;
}
.pos-custom-card.border-primary .pos-card-title {
    color: #ffffff !important;
}
.pos-custom-card.border-primary .pos-card-amount {
    color: #bfdbfe !important;
}
/* Input area inside card */
.pos-custom-card .mode-of-payment-control {
    display: none;
    padding: 0 16px 14px 16px;
    background: inherit;
}
.pos-custom-card.border-primary .mode-of-payment-control {
    display: block !important;
}
.pos-custom-card .mode-of-payment-control .frappe-control {
    margin: 0 !important;
}
/* Cash shortcuts */
.pos-custom-card .cash-shortcuts {
    display: none;
    grid-template-columns: repeat(3, 1fr);
    gap: 6px;
    padding: 0 16px 12px 16px;
    font-size: 13px;
    text-align: center;
}
.pos-custom-card.border-primary .cash-shortcuts {
    display: grid !important;
}
.pos-custom-card .cash-shortcuts .shortcut {
    background: rgba(255,255,255,0.25);
    border-radius: 6px;
    padding: 4px 6px;
    cursor: pointer;
    font-weight: 500;
    color: #fff;
    transition: background 0.1s;
}
.pos-custom-card .cash-shortcuts .shortcut:hover {
    background: rgba(255,255,255,0.4);
}
/* pay-amount hidden when selected (shown in title row instead) */
.pos-custom-card .pay-amount { display: none !important; }
        `;
        document.head.appendChild(style);
    }

    // ── Apply visual config to a single card element ─────────────────────
    function style_card($card, label, selected) {
        const cfg = get_config(label);
        if (!cfg) return;

        $card.addClass("pos-custom-card");

        if (selected) {
            $card.css({ background: cfg.bg_sel, "border-color": cfg.bg_sel });
        } else {
            $card.css({ background: cfg.bg_idle, "border-color": cfg.border_idle });
        }
    }

    // ── Rebuild the title row of a card ──────────────────────────────────
    function rebuild_title_row($card, label, amount_str) {
        const cfg = get_config(label);
        const icon = cfg ? cfg.icon : "💳";

        $card.find(".pos-card-title").remove();

        const $title = $(`
            <div class="pos-card-title">
                <span class="pos-card-icon">${icon}</span>
                <span class="pos-card-label">${label}</span>
                <span class="pos-card-amount">${amount_str || ""}</span>
            </div>
        `);

        $card.prepend($title);
    }

    // ── Main hook: runs after payment section renders ────────────────────
    function patch_payment_ui() {
        const pos = window.cur_pos;
        if (!pos || !pos.payment) return;

        const payment = pos.payment;
        const $modes = payment.$payment_modes;
        if (!$modes || !$modes.length) return;

        inject_styles();

        $modes.find(".mode-of-payment").each(function () {
            const $card = $(this);
            if ($card.hasClass("pos-custom-card")) return; // already patched

            const mode = $card.attr("data-mode");
            if (!mode) return;

            // Identify label from existing text node (first text node child)
            let label = "";
            $card.contents().filter(function () {
                return this.nodeType === 3; // text node
            }).each(function () {
                const t = this.nodeValue.trim();
                if (t) label = t;
            });
            if (!label) label = mode.replace(/_/g, " ");

            const $amount_el = $card.find(`.${mode}-amount`);
            const amount_str = $amount_el.text().trim();

            const selected = $card.hasClass("border-primary");
            rebuild_title_row($card, label, amount_str);
            style_card($card, label, selected);
        });

        // Observe selection changes
        observe_selection($modes, payment);
        // Patch onchange to prevent overpayment + auto-distribute
        patch_amount_controls(payment);
    }

    // ── Watch for border-primary changes ────────────────────────────────
    function observe_selection($modes, payment) {
        if ($modes.data("pos-observer")) return;
        $modes.data("pos-observer", true);

        const observer = new MutationObserver(() => {
            $modes.find(".mode-of-payment.pos-custom-card").each(function () {
                const $card = $(this);
                const mode = $card.attr("data-mode");
                const selected = $card.hasClass("border-primary");
                const cfg = get_config(get_label_for_mode($modes, mode));
                if (!cfg) return;

                if (selected) {
                    $card.css({ background: cfg.bg_sel, "border-color": "#1d4ed8" });
                    $card.find(".pos-card-title").css("color", "#ffffff");
                    $card.find(".pos-card-amount").css("color", "#bfdbfe");
                } else {
                    $card.css({ background: cfg.bg_idle, "border-color": cfg.border_idle });
                    $card.find(".pos-card-title").css("color", "#1e293b");
                    $card.find(".pos-card-amount").css("color", "#374151");
                }
            });
        });

        observer.observe($modes[0], { attributes: true, subtree: true, attributeFilter: ["class"] });
    }

    function get_label_for_mode($modes, mode) {
        const $card = $modes.find(`.mode-of-payment[data-mode="${mode}"]`);
        return $card.find(".pos-card-label").text().trim();
    }

    // ── Patch amount controls for overpayment guard + auto-distribute ────
    function patch_amount_controls(payment) {
        if (payment._pos_payment_ui_patched) return;
        payment._pos_payment_ui_patched = true;

        // Priority order for distribution: Espèces → Chèque → Dette
        const PRIORITY = [/esp[eè]/i, /ch[eè]que/i, /dette/i];

        function get_payments() {
            const frm = payment.events.get_frm();
            return (frm && frm.doc && frm.doc.payments) || [];
        }

        function get_grand_total() {
            const frm = payment.events.get_frm();
            const doc = frm && frm.doc;
            if (!doc) return 0;
            return cint(frappe.sys_defaults.disable_rounded_total) ? doc.grand_total : doc.rounded_total;
        }

        function sanitize(m) { return payment.sanitize_mode_of_payment(m); }

        function get_control(label_match) {
            const payments = get_payments();
            for (const p of payments) {
                if (label_match.test(p.mode_of_payment)) {
                    const mode = sanitize(p.mode_of_payment);
                    return { control: payment[`${mode}_control`], p, mode };
                }
            }
            return null;
        }

        function sum_other_modes(excluding_mode) {
            const payments = get_payments();
            let total = 0;
            for (const p of payments) {
                if (sanitize(p.mode_of_payment) !== excluding_mode) {
                    total += flt(p.amount);
                }
            }
            return total;
        }

        // Override each known control's onchange
        function patch_control(entry) {
            if (!entry || !entry.control) return;
            const { control, p, mode } = entry;
            const orig_onchange = control.df.onchange;
            control.df.onchange = function () {
                const grand_total = get_grand_total();
                const other_sum = sum_other_modes(mode);
                const max_allowed = Math.max(0, grand_total - other_sum);
                let val = flt(this.value);

                if (val > max_allowed) {
                    val = max_allowed;
                    // Silently cap
                    control.set_value(val);
                    frappe.show_alert({ message: __("Montant limité au solde restant."), indicator: "orange" });
                }

                if (orig_onchange) orig_onchange.call(this);

                // Update displayed amount in card title
                const formatted = val > 0 ? format_currency(val, (payment.events.get_frm().doc || {}).currency) : "";
                payment.$payment_modes
                    .find(`.mode-of-payment[data-mode="${mode}"] .pos-card-amount`)
                    .text(formatted);
            };
        }

        // Wait for controls to be created then patch them
        setTimeout(() => {
            for (const rx of PRIORITY) {
                patch_control(get_control(rx));
            }

            // Also override auto_set_remaining_amount to distribute in priority order
            payment._orig_auto_set = payment.auto_set_remaining_amount.bind(payment);
            payment.auto_set_remaining_amount = function () {
                const grand_total = get_grand_total();
                const frm = this.events.get_frm();
                const doc = frm && frm.doc;
                if (!doc) return;

                const selected_mode_label = this.selected_mode
                    ? (this.$payment_modes.find(".mode-of-payment.border-primary .pos-card-label").text().trim())
                    : "";

                // If the selected mode already has a value, don't override
                const cur_val = this.selected_mode ? flt(this.selected_mode.get_value()) : 0;
                if (cur_val > 0) return;

                // Calculate total already set in other modes
                const other_sum = this.selected_mode
                    ? sum_other_modes(this.selected_mode.df
                        ? sanitize(this.selected_mode.df.label || "")
                        : "")
                    : flt(doc.paid_amount);

                const remaining = grand_total - other_sum;
                if (remaining > 0 && this.selected_mode) {
                    this.selected_mode.set_value(remaining);
                }
            };
        }, 300);
    }

    // ── Poll until POS is ready ──────────────────────────────────────────
    let poll_attempts = 0;
    const poll = setInterval(function () {
        poll_attempts++;
        if (poll_attempts > 300) { clearInterval(poll); return; }

        const pos = window.cur_pos;
        if (!pos || !pos.payment || !pos.payment.$payment_modes) return;

        const $modes = pos.payment.$payment_modes;
        if ($modes.find(".mode-of-payment").length === 0) return;

        clearInterval(poll);
        patch_payment_ui();

        // Re-apply on every payment section re-render
        const orig_render = pos.payment.render_payment_mode_dom.bind(pos.payment);
        pos.payment.render_payment_mode_dom = function () {
            orig_render();
            setTimeout(() => patch_payment_ui(), 50);
        };
    }, 500);
})();
