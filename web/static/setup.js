// ── Battery options — EU-only fallback (server is authoritative) ─────────────
const BATTERY_OPTIONS = {
  T03: [{ v: "37.3", label: "37.3 kWh" }],
  C10: [
    { v: "69.9", label: "69.9 kWh — RWD" },
    { v: "81.9", label: "81.9 kWh — AWD" },
    { v: "28.4", label: "28.4 kWh — REEV (range-extender)", reev: true },
  ],
  B10: [
    { v: "56.2", label: "56.2 kWh — Pro · 361 km WLTP" },
    { v: "67.1", label: "67.1 kWh — Pro Max · 434 km WLTP" },
    { v: "18.8", label: "18.8 kWh — REEV (range-extender)", reev: true },
  ],
};

// ── i18n strings ─────────────────────────────────────────────────────────────
const strings = {
  en: {
    title:         "Welcome to LeapMotor Mate",
    subtitle:      "Choose how to get started",
    acctWarn:      "⚠️ Use a Leapmotor account dedicated to Mate only. Don't use this account in any other app, add-on, Docker or integration at the same time — they evict each other's session and you'll get missing or inconsistent data.",
    email:         "Leapmotor account email",
    emailPh:       "you@example.com",
    password:      "Password",
    pin:           "Operation PIN",
    pinPh:         "4-digit PIN",
    detectBtn:     "🔍 Detect My Car",
    detecting:     "⏳ Detecting…",
    detected:      "Car detected",
    carLine:       (model, vin) => `Leapmotor ${model} · VIN ···${vin.slice(-6)}`,
    batteryLabel:  "Battery pack",
    batteryHint:   "Select your battery variant",
    batteryAutoSet:"Battery detected (EU spec)",
    detectFail:    "Could not detect vehicle",
    manualEntry:   "Enter battery capacity manually (kWh)",
    manualHint:    "B10 Pro: 56.2 · B10 Pro Max: 67.1 · T03: 37.3 · C10 RWD: 69.9 · C10 AWD: 81.9",
    submit:        "Connect & Start",
    required:      "Email, password and PIN are required.",
    noBattery:     "Please select a battery variant first.",
    certTitle:     "Step 1 — App certificate",
    certDesc:      "Mate needs the Leapmotor app TLS certificate (the same for everyone, not your account). Download the two files app.crt and app.key from:",
    certBtn:       "Save certificate",
    certSaving:    "⏳ Saving…",
    certToPaste:   "Paste the PEM text instead",
    certToFile:    "Upload the files instead",
    certBoth:      "Both app.crt and app.key are required.",
    certSaved:     "✓ Certificate saved",
    certCrtLabel:  "app.crt (certificate)",
    certKeyLabel:  "app.key (private key)",
    setupChoice:   "Set up my car",
    back:          "Back",
    demoBtn:       "Try the demo",
    demoNote:      "Sample data only — nothing is real. You can exit anytime.",
    demoStarting:  "Starting the demo…",
  },
  it: {
    title:         "Benvenuto in LeapMotor Mate",
    subtitle:      "Scegli come iniziare",
    acctWarn:      "⚠️ Usa un account Leapmotor dedicato solo a Mate. Non usare questo account in altre app, add-on, Docker o integrazioni allo stesso tempo — si sfrattano la sessione a vicenda e avrai dati mancanti o incoerenti.",
    email:         "Email account Leapmotor",
    emailPh:       "tu@esempio.com",
    password:      "Password",
    pin:           "PIN operativo",
    pinPh:         "PIN a 4 cifre",
    detectBtn:     "🔍 Rileva la mia auto",
    detecting:     "⏳ Rilevamento…",
    detected:      "Auto rilevata",
    carLine:       (model, vin) => `Leapmotor ${model} · VIN ···${vin.slice(-6)}`,
    batteryLabel:  "Pacco batteria",
    batteryHint:   "Seleziona la variante batteria",
    batteryAutoSet:"Batteria rilevata (spec. europea)",
    detectFail:    "Impossibile rilevare il veicolo",
    manualEntry:   "Inserisci la capacità batteria manualmente (kWh)",
    manualHint:    "B10 Pro: 56.2 · B10 Pro Max: 67.1 · T03: 37.3 · C10 RWD: 69.9 · C10 AWD: 81.9",
    submit:        "Connetti e avvia",
    required:      "Email, password e PIN sono obbligatori.",
    noBattery:     "Seleziona prima una variante batteria.",
    certTitle:     "Passo 1 — Certificato app",
    certDesc:      "Mate ha bisogno del certificato TLS dell'app Leapmotor (uguale per tutti, non del tuo account). Scarica i due file app.crt e app.key da:",
    certBtn:       "Salva certificato",
    certSaving:    "⏳ Salvataggio…",
    certToPaste:   "Incolla il testo PEM invece",
    certToFile:    "Carica i file invece",
    certBoth:      "Servono entrambi app.crt e app.key.",
    certSaved:     "✓ Certificato salvato",
    certCrtLabel:  "app.crt (certificato)",
    certKeyLabel:  "app.key (chiave privata)",
    setupChoice:   "Configura la mia auto",
    back:          "Indietro",
    demoBtn:       "Prova la demo",
    demoNote:      "Solo dati di esempio — niente è reale. Puoi uscire quando vuoi.",
    demoStarting:  "Avvio della demo…",
  },
  fr: {
    title:         "Bienvenue sur LeapMotor Mate",
    subtitle:      "Choisissez comment commencer",
    email:         "E-mail du compte Leapmotor",
    emailPh:       "vous@exemple.com",
    password:      "Mot de passe",
    pin:           "Code PIN d'opération",
    pinPh:         "PIN à 4 chiffres",
    detectBtn:     "🔍 Détecter ma voiture",
    detecting:     "⏳ Détection…",
    detected:      "Voiture détectée",
    carLine:       (model, vin) => `Leapmotor ${model} · VIN ···${vin.slice(-6)}`,
    batteryLabel:  "Pack batterie",
    batteryHint:   "Sélectionnez votre variante de batterie",
    batteryAutoSet:"Batterie détectée (spéc. UE)",
    detectFail:    "Impossible de détecter le véhicule",
    manualEntry:   "Saisir la capacité batterie manuellement (kWh)",
    manualHint:    "B10 Pro: 56.2 · B10 Pro Max: 67.1 · T03: 37.3 · C10 RWD: 69.9 · C10 AWD: 81.9",
    submit:        "Connecter et démarrer",
    required:      "E-mail, mot de passe et PIN sont obligatoires.",
    noBattery:     "Veuillez d'abord sélectionner une variante de batterie.",
    certTitle:     "Étape 1 — Certificat de l'app",
    certDesc:      "Mate a besoin du certificat TLS de l'app Leapmotor (le même pour tous, pas celui de votre compte). Téléchargez les deux fichiers app.crt et app.key depuis :",
    certBtn:       "Enregistrer le certificat",
    certSaving:    "⏳ Enregistrement…",
    certToPaste:   "Coller le texte PEM à la place",
    certToFile:    "Téléverser les fichiers à la place",
    certBoth:      "app.crt et app.key sont tous deux requis.",
    certSaved:     "✓ Certificat enregistré",
    certCrtLabel:  "app.crt (certificat)",
    certKeyLabel:  "app.key (clé privée)",
    setupChoice:   "Configurer ma voiture",
    back:          "Retour",
    demoBtn:       "Essayer la démo",
    demoNote:      "Données d'exemple uniquement — rien n'est réel. Vous pouvez quitter à tout moment.",
    demoStarting:  "Démarrage de la démo…",
  },
  de: {
    title:         "Willkommen bei LeapMotor Mate",
    subtitle:      "Wählen Sie, wie Sie starten möchten",
    email:         "E-Mail des Leapmotor-Kontos",
    emailPh:       "sie@beispiel.com",
    password:      "Passwort",
    pin:           "Bedien-PIN",
    pinPh:         "4-stellige PIN",
    detectBtn:     "🔍 Mein Auto erkennen",
    detecting:     "⏳ Erkennung…",
    detected:      "Auto erkannt",
    carLine:       (model, vin) => `Leapmotor ${model} · VIN ···${vin.slice(-6)}`,
    batteryLabel:  "Batteriepaket",
    batteryHint:   "Wählen Sie Ihre Batterievariante",
    batteryAutoSet:"Batterie erkannt (EU-Spezifikation)",
    detectFail:    "Fahrzeug konnte nicht erkannt werden",
    manualEntry:   "Batteriekapazität manuell eingeben (kWh)",
    manualHint:    "B10 Pro: 56.2 · B10 Pro Max: 67.1 · T03: 37.3 · C10 RWD: 69.9 · C10 AWD: 81.9",
    submit:        "Verbinden & starten",
    required:      "E-Mail, Passwort und PIN sind erforderlich.",
    noBattery:     "Bitte wählen Sie zuerst eine Batterievariante.",
    certTitle:     "Schritt 1 — App-Zertifikat",
    certDesc:      "Mate benötigt das TLS-Zertifikat der Leapmotor-App (für alle gleich, nicht Ihr Konto). Laden Sie die beiden Dateien app.crt und app.key herunter von:",
    certBtn:       "Zertifikat speichern",
    certSaving:    "⏳ Speichern…",
    certToPaste:   "Stattdessen PEM-Text einfügen",
    certToFile:    "Stattdessen Dateien hochladen",
    certBoth:      "app.crt und app.key sind beide erforderlich.",
    certSaved:     "✓ Zertifikat gespeichert",
    certCrtLabel:  "app.crt (Zertifikat)",
    certKeyLabel:  "app.key (privater Schlüssel)",
    setupChoice:   "Mein Auto einrichten",
    back:          "Zurück",
    demoBtn:       "Demo ausprobieren",
    demoNote:      "Nur Beispieldaten — nichts ist echt. Sie können jederzeit beenden.",
    demoStarting:  "Demo wird gestartet…",
  },
  pl: {
    title:         "Witaj w LeapMotor Mate",
    subtitle:      "Wybierz, jak zacząć",
    acctWarn:      "⚠️ Użyj konta Leapmotor przeznaczonego wyłącznie dla Mate. Nie używaj tego konta jednocześnie w innych aplikacjach, dodatkach, Dockerze ani integracjach — wzajemnie wyrzucają sobie sesję i otrzymasz brakujące lub niespójne dane.",
    email:         "E-mail konta Leapmotor",
    emailPh:       "ty@przyklad.com",
    password:      "Hasło",
    pin:           "PIN operacyjny",
    pinPh:         "4-cyfrowy PIN",
    detectBtn:     "🔍 Wykryj mój samochód",
    detecting:     "⏳ Wykrywanie…",
    detected:      "Samochód wykryty",
    carLine:       (model, vin) => `Leapmotor ${model} · VIN ···${vin.slice(-6)}`,
    batteryLabel:  "Pakiet baterii",
    batteryHint:   "Wybierz wariant baterii",
    batteryAutoSet:"Bateria wykryta (specyfikacja UE)",
    detectFail:    "Nie udało się wykryć pojazdu",
    manualEntry:   "Wprowadź pojemność baterii ręcznie (kWh)",
    manualHint:    "B10 Pro: 56.2 · B10 Pro Max: 67.1 · T03: 37.3 · C10 RWD: 69.9 · C10 AWD: 81.9",
    submit:        "Połącz i uruchom",
    required:      "E-mail, hasło i PIN są wymagane.",
    noBattery:     "Najpierw wybierz wariant baterii.",
    certTitle:     "Krok 1 — Certyfikat aplikacji",
    certDesc:      "Mate potrzebuje certyfikatu TLS aplikacji Leapmotor (takiego samego dla wszystkich, nie Twojego konta). Pobierz dwa pliki app.crt i app.key z:",
    certBtn:       "Zapisz certyfikat",
    certSaving:    "⏳ Zapisywanie…",
    certToPaste:   "Zamiast tego wklej tekst PEM",
    certToFile:    "Zamiast tego prześlij pliki",
    certBoth:      "Wymagane są oba pliki: app.crt i app.key.",
    certSaved:     "✓ Certyfikat zapisany",
    certCrtLabel:  "app.crt (certyfikat)",
    certKeyLabel:  "app.key (klucz prywatny)",
    setupChoice:   "Skonfiguruj mój samochód",
    back:          "Wstecz",
    demoBtn:       "Wypróbuj demo",
    demoNote:      "Tylko przykładowe dane — nic nie jest prawdziwe. Możesz wyjść w każdej chwili.",
    demoStarting:  "Uruchamianie demo…",
  },
};

let currentLang = 'en';
let certPasteMode = false;

// ── Certificate step ──────────────────────────────────────────────────────────
function toggleCertMode() {
  certPasteMode = !certPasteMode;
  document.getElementById('cert-mode-file').style.display  = certPasteMode ? 'none' : 'block';
  document.getElementById('cert-mode-paste').style.display = certPasteMode ? 'block' : 'none';
  document.getElementById('cert-toggle-mode').textContent  =
    certPasteMode ? strings[currentLang].certToFile : strings[currentLang].certToPaste;
}

function showCertError(msg) {
  const el = document.getElementById('cert-error');
  el.textContent = msg;
  el.style.display = msg ? 'block' : 'none';
}

async function saveCert() {
  const s = strings[currentLang];
  showCertError('');
  const fd = new FormData();
  if (certPasteMode) {
    const crt = document.getElementById('paste-crt').value.trim();
    const key = document.getElementById('paste-key').value.trim();
    if (!crt || !key) { showCertError(s.certBoth); return; }
    fd.append('crt_pem', crt);
    fd.append('key_pem', key);
  } else {
    const crt = document.getElementById('file-crt').files[0];
    const key = document.getElementById('file-key').files[0];
    if (!crt || !key) { showCertError(s.certBoth); return; }
    fd.append('crt_file', crt);
    fd.append('key_file', key);
  }

  const btn = document.getElementById('btn-cert');
  document.getElementById('cert-btn-label').textContent = s.certSaving;
  btn.disabled = true; btn.style.opacity = '0.6';
  try {
    const resp = await fetch('api/setup/cert', { method: 'POST', body: fd });
    const data = await resp.json();
    if (!resp.ok || data.error) { showCertError(data.error || 'Error'); return; }
    // Cert saved → reveal the login step
    document.getElementById('cert-step').style.display = 'none';
    document.getElementById('setup-form').style.display = 'block';
  } catch (err) {
    showCertError(err.message);
  } finally {
    document.getElementById('cert-btn-label').textContent = s.certBtn;
    btn.disabled = false; btn.style.opacity = '1';
  }
}

// ── Language switch ───────────────────────────────────────────────────────────
function setLang(lang) {
  currentLang = lang;
  const s = strings[lang];
  document.getElementById('lang-input').value            = lang;
  document.getElementById('setup-title').textContent     = s.title;
  document.getElementById('setup-subtitle').textContent  = s.subtitle;
  document.getElementById('lbl-email').textContent       = s.email;
  document.getElementById('acct-warn').textContent       = s.acctWarn;
  document.getElementById('inp-email').placeholder       = s.emailPh;
  document.getElementById('lbl-password').textContent    = s.password;
  document.getElementById('lbl-pin').textContent         = s.pin;
  document.getElementById('inp-pin').placeholder         = s.pinPh;
  document.getElementById('detect-label').textContent    = s.detectBtn;
  document.getElementById('submit-label').textContent    = s.submit;
  document.getElementById('cert-title').textContent      = s.certTitle;
  document.getElementById('cert-desc').textContent       = s.certDesc;
  document.getElementById('cert-btn-label').textContent  = s.certBtn;
  document.getElementById('cert-toggle-mode').textContent = certPasteMode ? s.certToFile : s.certToPaste;
  document.getElementById('lbl-crt').textContent          = s.certCrtLabel;
  document.getElementById('lbl-key').textContent          = s.certKeyLabel;
  document.getElementById('choice-setup-label').textContent = s.setupChoice;
  document.getElementById('demo-cta-label').textContent     = s.demoBtn;
  document.getElementById('demo-cta-note').textContent      = s.demoNote;
  document.getElementById('back-label').textContent         = s.back;

  const active   = 'px-4 py-1.5 rounded-full text-sm font-medium border transition-all cursor-pointer bg-brand/20 border-brand text-brand';
  const inactive = 'px-4 py-1.5 rounded-full text-sm font-medium border transition-all cursor-pointer border-slate-600 text-slate-400 hover:border-slate-400';
  document.getElementById('btn-en').className = lang === 'en' ? active : inactive;
  document.getElementById('btn-it').className = lang === 'it' ? active : inactive;
  document.getElementById('btn-fr').className = lang === 'fr' ? active : inactive;
  document.getElementById('btn-de').className = lang === 'de' ? active : inactive;
  document.getElementById('btn-pl').className = lang === 'pl' ? active : inactive;
}

// ── Demo mode ─────────────────────────────────────────────────────────────────
// Enter demo from inside Mate: write the flag server-side, then the container restarts
// into demo and we send the browser to the dashboard once it's back up.
async function startDemo() {
  const s = strings[currentLang];
  const btn = document.getElementById('demo-cta-btn');
  btn.disabled = true;
  document.getElementById('demo-cta-label').textContent = s.demoStarting;
  try {
    const r = await fetch('api/demo/enable', { method: 'POST' });
    const d = await r.json();
    if (d.restarting) { waitForRestart(s.demoStarting, true); return; }
    // Not restarting (already configured) — restore the button.
    btn.disabled = false;
    document.getElementById('demo-cta-label').textContent = s.demoBtn;
  } catch (err) {
    btn.disabled = false;
    document.getElementById('demo-cta-label').textContent = s.demoBtn;
  }
}

// Full-screen overlay shown during the restart. Polls /api/demo/status until THIS container
// has come back in the target mode (demo=true after enable), then navigates home. Polling the
// MODE — not an up/down transition — is robust to a sub-second restart that "down then up"
// detection would miss (which left the spinner hanging). Resolves home against <base> so it
// works under HA ingress.
function waitForRestart(msg, wantDemo) {
  const ov = document.createElement('div');
  ov.style.cssText = 'position:fixed;inset:0;z-index:9999;background:#0f172a;display:flex;'
    + 'flex-direction:column;align-items:center;justify-content:center;gap:18px;color:#e2e8f0;'
    + 'text-align:center;padding:24px';
  ov.innerHTML = '<div style="width:42px;height:42px;border:4px solid #334155;'
    + 'border-top-color:#14b8a6;border-radius:50%;animation:lmspin 1s linear infinite"></div>'
    + '<div style="font-size:15px">' + msg + '</div>'
    + '<style>@keyframes lmspin{to{transform:rotate(360deg)}}</style>';
  document.body.appendChild(ov);
  const home = (document.querySelector('base') || {}).href || './';
  let tries = 0;
  const poll = () => {
    tries++;
    fetch('api/demo/status', { cache: 'no-store' })
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(d => {
        if (!!d.demo === wantDemo || tries > 75) { window.location.href = home; return; }
        setTimeout(poll, 1000);
      })
      .catch(() => { if (tries > 75) { window.location.href = home; return; } setTimeout(poll, 1000); });
  };
  setTimeout(poll, 1200);   // let the 1.2s server-side restart timer fire first
}

// ── Battery selector ──────────────────────────────────────────────────────────
function selectBattery(v, reev) {
  document.getElementById('h-battery').value = v;
  document.getElementById('h-is-reev').value = reev ? '1' : '0';
  document.querySelectorAll('.bat-opt').forEach(el => {
    const sel = el.dataset.v === v;
    el.style.background   = sel ? '#14b8a622' : '#1e293b';
    el.style.borderColor  = sel ? '#14b8a6'   : '#334155';
    const dot = el.querySelector('.bat-dot');
    dot.style.background   = sel ? '#14b8a6' : 'transparent';
    dot.style.borderColor  = sel ? '#14b8a6' : '#475569';
  });
}

function buildBatterySelector(options) {
  const s = strings[currentLang];
  let html = `<div style="font-size:13px;font-weight:500;color:#cbd5e1;margin-bottom:8px">${s.batteryLabel}</div>`;
  html += `<div style="display:flex;flex-direction:column;gap:8px">`;
  options.forEach(opt => {
    html += `
      <div class="bat-opt" data-v="${opt.v}"
           onclick="selectBattery('${opt.v}', ${opt.reev ? 'true' : 'false'})"
           style="display:flex;align-items:center;gap:12px;padding:12px 16px;border-radius:12px;
                  border:1px solid #334155;background:#1e293b;cursor:pointer;transition:all .15s">
        <div class="bat-dot" style="width:14px;height:14px;border-radius:50%;border:2px solid #475569;
                                    flex-shrink:0;transition:all .15s;background:transparent"></div>
        <span style="color:#f1f5f9;font-size:14px;font-weight:500">${opt.label}</span>
      </div>`;
  });
  html += `</div>`;
  return html;
}

function buildAutoBattery(kwh, label) {
  const s = strings[currentLang];
  document.getElementById('h-battery').value = kwh;
  return `
    <div style="background:#14b8a615;border:1px solid #14b8a640;border-radius:12px;
                padding:12px 16px;display:flex;align-items:center;gap:12px">
      <div style="font-size:22px;line-height:1">🔋</div>
      <div>
        <div style="color:#14b8a6;font-size:11px;font-weight:600;text-transform:uppercase;
                    letter-spacing:.05em">${s.batteryAutoSet}</div>
        <div style="color:#f1f5f9;font-size:15px;font-weight:700;margin-top:2px">${label}</div>
      </div>
    </div>`;
}

function buildManualInput() {
  const s = strings[currentLang];
  return `
    <div style="font-size:13px;font-weight:500;color:#cbd5e1;margin-bottom:8px">${s.manualEntry}</div>
    <input type="number" id="manual-battery" min="10" max="200" step="0.1" value="67.1"
           oninput="document.getElementById('h-battery').value=this.value"
           style="width:100%;padding:12px 16px;border-radius:12px;background:#0f172a;
                  border:1px solid #334155;color:#fff;font-size:14px;outline:none;box-sizing:border-box">
    <p style="font-size:11px;color:#475569;margin-top:6px">${s.manualHint}</p>`;
}

// ── Detection flow ────────────────────────────────────────────────────────────
async function detectCar() {
  const user = document.getElementById('inp-email').value.trim();
  const pwd  = document.getElementById('inp-password').value.trim();
  const pin  = document.getElementById('inp-pin').value.trim();
  const s    = strings[currentLang];

  if (!user || !pwd || !pin) {
    showResult(`
      <div style="background:#ef444422;border:1px solid #ef444455;border-radius:10px;
                  padding:12px 16px;color:#fca5a5;font-size:14px">
        ${s.required}
      </div>`);
    return;
  }

  // Loading
  const btn = document.getElementById('btn-detect');
  document.getElementById('detect-label').textContent = s.detecting;
  btn.disabled = true;
  btn.style.opacity = '0.6';
  document.getElementById('detect-result').style.display = 'none';

  try {
    const resp = await fetch('api/setup/detect-vehicle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user, password: pwd, pin }),
    });
    const data = await resp.json();

    if (!resp.ok || data.error) {
      // Failure: show error + manual input
      showResult(`
        <div style="background:#ef444422;border:1px solid #ef444455;border-radius:12px;padding:14px 16px">
          <div style="color:#fca5a5;font-size:13px;font-weight:600;margin-bottom:6px">✗ ${s.detectFail}</div>
          <div style="color:#fca5a5;font-size:12px;opacity:.8;margin-bottom:14px">${data.error || ''}</div>
          ${buildManualInput()}
        </div>`);
      document.getElementById('h-battery').value = '67.1';
    } else {
      // Success — save detected vehicle info in hidden fields
      document.getElementById('h-car-type').value = data.car_type || '';
      document.getElementById('h-vin').value       = data.vin       || '';

      // Build battery block based on what the server returns
      let batteryBlock;
      if (data.battery_kwh) {
        // Single EU variant: server auto-detected kWh — no user selection needed
        batteryBlock = buildAutoBattery(data.battery_kwh, data.battery_label);
      } else {
        const options = (data.battery_options && data.battery_options.length)
          ? data.battery_options
          : (BATTERY_OPTIONS[data.car_type] || []);
        if (options.length > 0) {
          batteryBlock = buildBatterySelector(options);
          selectBattery(options[0].v);
        } else {
          batteryBlock = buildManualInput();
          document.getElementById('h-battery').value = '67.1';
        }
      }

      showResult(`
        <div style="background:#14b8a615;border:1px solid #14b8a640;border-radius:12px;
                    padding:14px 16px;margin-bottom:12px;display:flex;align-items:center;gap:12px">
          <div style="font-size:28px;line-height:1">🚗</div>
          <div>
            <div style="color:#14b8a6;font-size:12px;font-weight:600;text-transform:uppercase;
                        letter-spacing:.05em">${s.detected}</div>
            <div style="color:#f1f5f9;font-size:16px;font-weight:700;margin-top:2px">
              ${s.carLine(data.car_type, data.vin)}
            </div>
          </div>
        </div>
        ${batteryBlock}`);
    }

    document.getElementById('btn-submit').style.display = 'block';
  } catch (err) {
    showResult(`
      <div style="background:#ef444422;border:1px solid #ef444455;border-radius:12px;padding:14px 16px">
        <div style="color:#fca5a5;font-size:13px;font-weight:600;margin-bottom:10px">
          ✗ ${s.detectFail}: ${err.message}
        </div>
        ${buildManualInput()}
      </div>`);
    document.getElementById('h-battery').value = '67.1';
    document.getElementById('btn-submit').style.display = 'block';
  } finally {
    document.getElementById('detect-label').textContent = s.detectBtn;
    btn.disabled = false;
    btn.style.opacity = '1';
  }
}

function showResult(html) {
  const el = document.getElementById('detect-result');
  el.innerHTML = html;
  el.style.display = 'block';
}

// ── Form validation ───────────────────────────────────────────────────────────
function validateSubmit() {
  const battery = document.getElementById('h-battery').value.trim();
  if (!battery) {
    alert(strings[currentLang].noBattery);
    return false;
  }
  return true;
}

// ── Show/hide password ───────────────────────────────────────────────────────
function toggleVisibility(inputId, btn) {
  const inp = document.getElementById(inputId);
  const isHidden = inp.type === 'password';
  inp.type = isHidden ? 'text' : 'password';
  btn.innerHTML = isHidden
    ? `<svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.542-7
             a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878
             l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29
             M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.542 7
             a10.025 10.025 0 01-4.132 5.411m0 0L21 21"/>
      </svg>`
    : `<svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/>
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7
             -1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/>
      </svg>`;
}

// ── Init ─────────────────────────────────────────────────────────────────────
const initLang = navigator.language.startsWith('it') ? 'it'
               : navigator.language.startsWith('fr') ? 'fr'
               : navigator.language.startsWith('de') ? 'de'
               : navigator.language.startsWith('pl') ? 'pl' : 'en';
setLang((document.body.dataset.prefillLang || '') || initLang);

// ── Landing choice ────────────────────────────────────────────────────────────
// "Set up my car" reveals the cert/login form (cert step first unless already present);
// the account form is never shown to a demo-curious user until they choose to configure.
async function chooseSetup() {
  document.getElementById('choice-step').style.display = 'none';
  document.getElementById('back-link').style.display = 'inline-block';
  try {
    const r = await fetch('api/setup/cert-status');
    const d = await r.json();
    if (d.present) {
      document.getElementById('setup-form').style.display = 'block';
    } else {
      document.getElementById('cert-step').style.display = 'block';
    }
  } catch {
    // On error, fall back to showing the login form (cert may be bundled)
    document.getElementById('setup-form').style.display = 'block';
  }
}

function backToChoice() {
  document.getElementById('cert-step').style.display = 'none';
  document.getElementById('setup-form').style.display = 'none';
  document.getElementById('back-link').style.display = 'none';
  document.getElementById('choice-step').style.display = 'block';
}

// The landing (#choice-step) is visible by default — nothing else is revealed on load.