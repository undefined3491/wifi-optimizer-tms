# Device recovery runbook — LCD / SteamOS Stable

Status: procedura przygotowana, nie wykonana. Dotyczy wyłącznie istniejącego urządzenia właściciela (LCD / SteamOS Stable) i nazwanych, zrecenzowanych kandydatów. Nie zamyka żadnego findings Stage 0 ani nie zastępuje [restoration design](../superpowers/specs/2026-10-16-st0-004-016-restoration-design.md) ani [recovery design](../superpowers/specs/2026-10-15-delivery-recovery-design.md) — oba pozostają pracą warunkową/propozycją, nie wdrożonym mechanizmem odzyskiwania.

Powiązane źródła (czytaj przed użyciem runbooka):

- [collect-inventory.md](collect-inventory.md) — jedyna autoryzowana procedura read-only.
- [decky-development-workflow.md](decky-development-workflow.md) — SSH jako transport opcjonalny, niebezpieczna ścieżka uninstall przy natywnym ZIP-replace.
- [device inventory 2026-10-16](results/2026-10-16-owner-device-inventory.md) — stan zastany (instalacja zmodyfikowana, nie baseline, nie backup).
- [device protocol](steam-deck-lcd-stable.md) — protokół porównań i zapisów wyników.
- [AGENTS.md](../../AGENTS.md) — obowiązek zachowania recovery records przed zmianą settings.

Konwencje ścieżek w tym runbooku (podstawić rzeczywiste wartości z §1 przed jakimkolwiek krokiem mutującym):

| Symbol | Znaczenie | Domyślna wartość na urządzeniu właściciela |
| --- | --- | --- |
| `$DECKY_HOME` | katalog home Decky | `/home/deck/homebrew` (potwierdzić na urządzeniu) |
| `$PLUGIN_DIR` | katalog instalacji wtyczki | `$DECKY_HOME/plugins/WiFi Optimizer` |
| `$SETTINGS_DIR` | katalog ustawień wtyczki | `$DECKY_HOME/settings/WiFi Optimizer` |
| `$BACKUP_DIR` | katalog backupu na Decku | `~/wifi-optimizer-recovery-<YYYYMMDD-HHMM>/` (poza repo, poza `$DECKY_HOME`) |
| `$OUT_DIR` | katalog dowodów na PC operatora | lokalny, ignorowany `out/device/owner-lcd-stable/` (nigdy nie commitować) |

Pliki systemowe (pełne ścieżki):

- Dispatcher: `/etc/NetworkManager/dispatcher.d/99-wifi-optimizer`
- Konfig NM wtyczki: `/etc/NetworkManager/conf.d/99-wifi-optimizer.conf`
- Modprobe wtyczki: `/etc/modprobe.d/99-wifi-optimizer.conf`
- Backend override wtyczki: `/etc/NetworkManager/conf.d/99-wifi-optimizer-backend.conf`
- Helper SteamOS (nie ruszać bez jawnej decyzji): `/etc/NetworkManager/conf.d/99-valve-wifi-backend.conf`

## 0. Zasady zgody — co wymaga jawnego „rób" właściciela

Bez jawnego potwierdzenia właściciela na dany krok wolno wykonać **wyłącznie** kroki read-only z §1 (kolektor standalone + odczyty `cat`/`nmcli show`/`systemctl status` bez sudo tam, gdzie to możliwe). Kolektor nie wymaga SSH ani CEF ani zmian konfiguracji.

Jawne „rób" (osobno na każdy punkt, nie blankietowo) jest wymagane przed:

1. Jakimkolwiek zapisem do `$SETTINGS_DIR`, `/etc/NetworkManager/…`, `/etc/modprobe.d/…` lub profili NM (w tym toggle opcji w panelu, `nmcli con mod`, `sysctl -w`, `iw`, `tc qdisc`, backend switch).
2. Uruchomieniem `install.sh`, kopiowaniem plików kandydata, `systemctl restart plugin_loader` / `stop` / `start`.
3. Naciśnięciem Reset / Uninstall w panelu (oba mają znane niebezpieczne założenia przywracania — patrz §4; Reset **nie jest** dowodem pristine baseline ani mechanizmem recovery).
4. Testami reconnect / sleep-wake / utraty AP (rozłączają sieć; wymagają lokalnego dostępu zapasowego do Decka).
5. Każdym użyciem `sudo` poza odczytem (`sudo -n` odmówione na tym urządzeniu 2026-10-16 — patrz inventory; hasło/polityka sudo bez zmian bez osobnej zgody).
6. Włączeniem CEF remote debugging lub React DevTools (tylko na zaufanej sieci, wyłączyć po sesji).
7. Udostępnieniem czegokolwiek z `$OUT_DIR` / `$BACKUP_DIR` poza maszynę właściciela (surowe raporty zawierają metadane urządzenia; SSID/BSSID/UUID/DNS i tak pomijamy w zapisach).

Zasada stop: brak odpowiedzi ≠ zgoda. Przerwanie kroku w połowie (timeout SSH, utrata WiFi, błąd sudo) oznacza stan **nieznany**, nie sukces — przejść do §4 (rollback), nie powtarzać mutacji „jeszcze raz".

## 1. Pre-mutation inventory (read-only, bez mutacji)

Cel: zamrozić obserwowalny stan przed jakąkolwiek mutacją. To jest obserwacja, **nie backup** — nie da się z niej odtworzyć wartości identyfikujących (SSID/BSSID/UUID/DNS), więc §2 (backup) jest obowiązkowy przed mutacją.

Warunki wstępne (sprawdzić, nie zmieniać):

- Panel WiFi Optimizer zamknięty; brak aktywnego switcha backendu / update / Reset.
- Brak celowych reconnectów, gier ani suspendu w trakcie kolekcji (snapshot jest nieatomowy; odnotować współbieżne zdarzenia).

Krok 1.1 — kolektor standalone (autoryzowany, bez IPC wtyczki, bez zmian). Na Decku w Konsole, w katalogu z plikiem kolektora:

```bash
umask 077
sudo /usr/bin/python3 ./collect_deck_inventory.py --decky-home "$HOME/homebrew" > "$HOME/wifi-optimizer-inventory.json"
```

- Sukces: exit `0`, plik JSON niepusty, brak wpisów `permission_denied` (lub świadomie odnotowane). Oczekiwanego `$HOME/homebrew` nie zgadywać — użyć rzeczywistego Decky home.
- Stop: nie-zerowy exit, pusty plik, błąd importu — zachować błąd i częściowy output, **nie zmieniać konfiguracji** żeby „uzyskać ładny raport".

Krok 1.2 — identyfikatory kandydata i środowiska (odczyt, zapisać do notatki sesji w `$OUT_DIR`):

```bash
cat "$DECKY_HOME/plugins/WiFi Optimizer/plugin.json"
cat "$DECKY_HOME/plugins/WiFi Optimizer/package.json"
sha256sum "$DECKY_HOME/plugins/WiFi Optimizer/plugin.json" \
  "$DECKY_HOME/plugins/WiFi Optimizer/package.json" \
  "$DECKY_HOME/plugins/WiFi Optimizer/main.py" \
  "$DECKY_HOME/plugins/WiFi Optimizer/dist/index.js" \
  "$DECKY_HOME/plugins/WiFi Optimizer/defaults/dispatcher.sh.tmpl"
systemctl --no-pager status plugin_loader.service
/usr/bin/nmcli -t -f NAME,UUID,TYPE con show
```

- Sukces: `plugin.json`/`package.json` dają nazwę/wersję (punkt odniesienia: instalacja zastana `0.11.6`, pakiet legacy `wifi-optimizer`), hashe dają się porównać z blobami Gita; `plugin_loader.service` to `active (running)`; lista profili bez rozłączania.
- Stop: brak katalogu `$PLUGIN_DIR` (instalacja nie istnieje — nie „naprawiać" instalacją bez zgody z §0), usługa nieaktywna (odnotować, nie startować bez zgody).

Krok 1.3 — zapis: plik JSON kolektora i notatkę sesji (czas UTC, SHA-256 kolektora i raportu, wersje, współbieżne zdarzenia) odłożyć do `$OUT_DIR` na PC operatora. Surowych raportów **nie commitować**; do `docs/testing/results/` trafia wyłącznie zwięzły, przejrzany wynik.

## 2. Backup (obowiązkowy przed mutacją)

Zakres: dokładnie te pliki i wykazy (nic więcej, nic mniej na tym etapie):

1. `$SETTINGS_DIR/settings.json` — aktualne flagi intencji (to nie są defaults).
2. `$SETTINGS_DIR/system_snapshot.json` — snapshot slotów owned-only (jeśli istnieje; brak pliku odnotować, nie tworzyć).
3. `$SETTINGS_DIR/last_enforced` — znacznik czasu ostatniego zastosowania.
4. `/etc/NetworkManager/conf.d/99-wifi-optimizer.conf` (jeśli istnieje; brak odnotować).
5. `/etc/modprobe.d/99-wifi-optimizer.conf` (jeśli istnieje; brak odnotować).
6. `/etc/NetworkManager/conf.d/99-wifi-optimizer-backend.conf` (obecność/brak rozstrzyga o stanie backend-override; instalacja zastana: absent).
7. `/etc/NetworkManager/conf.d/99-valve-wifi-backend.conf` — tylko kopia odczytana (zakaz modyfikacji bez osobnej decyzji).
8. `/etc/NetworkManager/dispatcher.d/99-wifi-optimizer` — kopia odczytana.
9. Wykaz profili NM (bez sekretów): `nmcli con show` + per aktywny profil `nmcli -t con show <UUID>` po oczyszczeniu z identyfikatorów przed archiwizacją.
10. `plugin.json`, `package.json`, hashe 6 plików produktu (jak w §1.2) — tożsamość tego, co backup dotyczy.

Procedura (wymaga zgody z §0 na odczyty sudo; to wciąż nie jest mutacja):

```bash
BACKUP_DIR="$HOME/wifi-optimizer-recovery-$(date -u +%Y%m%d-%H%M)"
mkdir -p "$BACKUP_DIR"
SETTINGS_DIR="$HOME/homebrew/settings/WiFi Optimizer"
/bin/cp -p "$SETTINGS_DIR/settings.json" "$BACKUP_DIR/settings.json"
/bin/cp -p "$SETTINGS_DIR/system_snapshot.json" "$BACKUP_DIR/system_snapshot.json" 2>/dev/null || echo "NOTE: no system_snapshot.json"
/bin/cp -p "$SETTINGS_DIR/last_enforced" "$BACKUP_DIR/last_enforced" 2>/dev/null || echo "NOTE: no last_enforced"
sudo /usr/bin/cat /etc/NetworkManager/conf.d/99-wifi-optimizer.conf > "$BACKUP_DIR/99-wifi-optimizer.conf" 2>/dev/null || echo "NOTE: no NM conf"
sudo /usr/bin/cat /etc/modprobe.d/99-wifi-optimizer.conf > "$BACKUP_DIR/99-modprobe.conf" 2>/dev/null || echo "NOTE: no modprobe conf"
sudo /usr/bin/cat /etc/NetworkManager/conf.d/99-wifi-optimizer-backend.conf > "$BACKUP_DIR/99-backend.conf" 2>/dev/null || echo "NOTE: no generic backend conf (expected on found device)"
sudo /usr/bin/cat /etc/NetworkManager/conf.d/99-valve-wifi-backend.conf > "$BACKUP_DIR/99-valve-wifi-backend.conf.REFONLY" 2>/dev/null || echo "NOTE: no valve helper conf"
sudo /usr/bin/cat /etc/NetworkManager/dispatcher.d/99-wifi-optimizer > "$BACKUP_DIR/99-dispatcher.REFONLY"
/usr/bin/nmcli con show > "$BACKUP_DIR/nmcli-con-show.txt"
# Scrub BEFORE any archiving or committing: replace every SSID/BSSID/UUID/DNS
# value in the copy with aliases (NET-A, AP-1, UUID-A, …) and save as
# "$BACKUP_DIR/nmcli-con-show.scrubbed.txt". Raw file stays mode 600 in
# $BACKUP_DIR only — never committed, never published. Committed docs and the
# validation matrix use aliases exclusively.
chmod 600 "$BACKUP_DIR"/*
sha256sum "$BACKUP_DIR"/* > "$BACKUP_DIR/SHA256SUMS"
cat "$BACKUP_DIR/SHA256SUMS"
```

- Sukces: każdy `cp`/`cat` ma parę „plik źródłowy istniał → kopia o niezerowym rozmiarze i wpisie w SHA256SUMS" albo świadome `NOTE: no …`; `SHA256SUMS` czyta się bez błędów (`sha256sum -c` na PC po skopiowaniu).
- Stop: błąd odczytu z uprawnień (odnotować `permission_denied`, nie obchodzić uprawnień), `$SETTINGS_DIR` nie istnieje (instalacja inna niż zakładana — przerwać, patrz założenia na końcu).

Weryfikacja backupu (przed mutacją, obowiązkowa):

```bash
ls -l "$BACKUP_DIR"
sha256sum -c "$BACKUP_DIR/SHA256SUMS"
python3 -c "import json;print(json.load(open('$BACKUP_DIR/settings.json')).keys())"
```

- Sukces: wszystkie sumy `OK`; `settings.json` parsuje się jako JSON.
- Stop: jakikolwiek `FAILED` albo błąd parsowania — nie przechodzić do §3; powtórzyć odczyt i odnotować.

Przechowywanie: oryginał backupu zostaje na Decku w `$BACKUP_DIR` (poza `$DECKY_HOME`, poza repo); kopia trafia na PC operatora do `$OUT_DIR/<data>-pre-<kandydat>/`. Surowe kopie zawierające identyfikatory sieci trzymać z prawami `600`, nie commitować, nie publikować.

Ograniczenie (uczciwie): backup nie odtwarza stanu sprzed pierwszej instalacji wtyczki na tym urządzeniu (pre-plugin baseline jest nieznany — instalacja zastana była już zmodyfikowana przez Optimize Safe). Przywraca wyłącznie stan „sprzed tej sesji mutacji".

## 3. Deploy kandydata (krok po kroku)

Reguła nadrzędna: testujemy **nazwany, zrecenzowany kandydat** (commit SHA + wersja pakietu + hashe `dist/`). Nie publikujemy żadnego Stable-release żeby „mieć fixture". Dwie dozwolone ścieżki — wybrać jedną za zgodą właściciela:

- **(A) `install.sh`-rescue**: preferowana dla wierności z instalacją manualną; pobiera archiwum resolved-comm SzA i podmienia katalog wtyczki.
- **(B) kopia plików + restart loadera**: tylko dla szybkiej iteracji deweloperskiej; nie dowodzi poprawności ścieżki install/update.

Wspólny preflight (obie ścieżki, read-only + jedna komenda pojemności):

```bash
df -h "$HOME/homebrew" /tmp
systemctl --no-pager is-active plugin_loader.service
/usr/bin/nmcli -t -f NAME,UUID,TYPE con show | head
```

- Sukces: wolne miejsce na pobranie/staging (brak ENOSPC z wyprzedzeniem), loader `active`, WiFi połączone.
- Stop: loader nieaktywny / brak miejsca / brak połączenia — nie deployować „w ciemno"; wrócić do §1.

Ścieżka (A) — install.sh (skrypt kopiowany na Deck przez operatora z walidacyjnego
checkoutu przez `scp`, SHA commita przypięty do kandydata z rekordu kandydata
(`docs/testing/results/2026-10-17-candidate-a3bb26b.md` §1) — nigdy `./install.sh`
„które tam leży"):

```bash
cd "$HOME"
bash ./install.sh 2>&1 | tee "$BACKUP_DIR/install-$(date -u +%Y%m%d-%H%M).log"
```

(Zakres `install.sh`: podmienia wyłącznie `$PLUGIN_DIR`, tworzy `py_modules/.keep`; **nie dotyka settings** — settings żyją poza drzewem wtyczki.)

- Sukces: log kończy się komunikatem instalacji bez błędów `mv`/`grep manifest`; `plugin.json` w `$PLUGIN_DIR` pokazuje nową nazwę/wersję kandydata.
- Stop: błąd pobrania (HTTP/redirect spoza HTTPS — nie podmieniać źródła „awaryjnie" na main/upstream), błąd manifestu (`name` nie `WiFi Optimizer TMS`), błąd miejsca — instalacja przerwana, stan nieznany → §4.

Ścieżka (B) — kopia plików + restart (wymaga zgody na restart loadera):

```bash
/bin/cp -p plugin.json package.json main.py decky.pyi "$DECKY_HOME/plugins/WiFi Optimizer/"
/bin/cp -p dist/index.js dist/index.js.map "$DECKY_HOME/plugins/WiFi Optimizer/dist/"
/bin/cp -p defaults/dispatcher.sh.tmpl "$DECKY_HOME/plugins/WiFi Optimizer/defaults/"
sudo /usr/bin/systemctl restart plugin_loader
sleep 8
systemctl --no-pager is-active plugin_loader.service
```

- Sukces: restart zwraca `active`; brak błędów kopiowania.
- Stop: usługa nie wraca do `active` w ~30 s → nie powtarzać restartu w pętli; zebrać `journalctl -u plugin_loader.service --since '-5 min'` (odczyt) i przejść do §4. Uwaga: Decky 3.2.10 przy natywnym ZIP-replace może wywołać `_uninstall()` (niebezpieczna ścieżka) — ten fakt dotyczy instalatora ZIP/URL, nie ścieżki (B), ale zabrania traktowania reinstall-ZIP jako „czystego baseline".

Weryfikacja wersji po starcie (obie ścieżki, obowiązkowa):

```bash
cat "$DECKY_HOME/plugins/WiFi Optimizer/package.json" | grep '"version"'
sha256sum "$DECKY_HOME/plugins/WiFi Optimizer/dist/index.js"
systemctl --no-pager status plugin_loader.service | head -5
```

- Sukces: wersja = wersja kandydata; hash `dist/index.js` = hash zbudowanego artefaktu kandydata; loader `active (running)`.
- Stop: wersja/hash niezgodne (stary bundle w pamięci lub częściowa podmiana) — nie testować dalej; odnotować `load_unconfirmed` i przejść do §4.

Dopiero po zielonej weryfikacji wolno (za osobną zgodą) wykonywać scenariusze z [device protocol](steam-deck-lcd-stable.md): jedna opcja naraz, powrót do stanu z backupu między porównaniami, jawne notowanie `Pass/Fail/Not tested`.

## 4. Rollback (kiedy i jak)

Red flags — którykolwiek oznacza natychmiastowy stop testów i rollback (nie „jeszcze jeden reconnect"):

- Brak WiFi po deployu albo po Resecie (profil nie wraca, `nmcli` pokazuje rozłączenie mimo prób `con up`).
- Loader nie wraca do `active` po restarcie (oba ścieżki §3).
- Wersja/hash po starcie niezgodne z kandydatem (`load_unconfirmed`).
- Błędy `errors[]` / `fallback:*` w wynikach operacji restore/reset (snapshot slot missing — fallback na assumed defaults, nie na stan sprzed sesji).
- Dowolny krok backupu/deploja zakończony stanem nieznanym (timeout, utrata SSH w trakcie zapisu, ENOSPC).
- Przypadkowe naciśnięcie Reset/Uninstall albo uruchomienie backend-switch bez przygotowanego planu powrotu.

Procedura rollback (wymaga zgody z §0; każdy krok z weryfikacją):

```bash
BACKUP_DIR="$HOME/wifi-optimizer-recovery-<DATA>"   # ten sam katalog co w §2
SETTINGS_DIR="$HOME/homebrew/settings/WiFi Optimizer"
sudo /usr/bin/systemctl stop plugin_loader
/bin/cp -p "$BACKUP_DIR/settings.json" "$SETTINGS_DIR/settings.json"
if [ -f "$BACKUP_DIR/system_snapshot.json" ]; then /bin/cp -p "$BACKUP_DIR/system_snapshot.json" "$SETTINGS_DIR/system_snapshot.json"; fi
if [ -f "$BACKUP_DIR/last_enforced" ]; then /bin/cp -p "$BACKUP_DIR/last_enforced" "$SETTINGS_DIR/last_enforced"; fi
if [ -f "$BACKUP_DIR/99-wifi-optimizer.conf" ]; then sudo /usr/bin/cp "$BACKUP_DIR/99-wifi-optimizer.conf" /etc/NetworkManager/conf.d/99-wifi-optimizer.conf; fi
if [ -f "$BACKUP_DIR/99-modprobe.conf" ]; then sudo /usr/bin/cp "$BACKUP_DIR/99-modprobe.conf" /etc/modprobe.d/99-wifi-optimizer.conf; fi
sudo /usr/bin/systemctl start plugin_loader
sleep 8
systemctl --no-pager is-active plugin_loader.service
/usr/bin/nmcli -t -f NAME,UUID,TYPE,ACTIVE con show | head
```

- Sukces każdego kroku: `stop` → usługa `inactive`; kopie bez błędów; `start` → `active`; `nmcli` pokazuje połączenie na oczekiwanym profilu.
- Stop: usługa nie startuje → zebrać journal (odczyt), nie wykonywać kolejnych mutacji; eskalować do właściciela z logiem i `$BACKUP_DIR/SHA256SUMS`.

Weryfikacja po rollbacku (obowiązkowa, porównać z §1/§2):

```bash
diff <(cat "$SETTINGS_DIR/settings.json") "$BACKUP_DIR/settings.json" && echo "SETTINGS MATCH"
sha256sum -c "$BACKUP_DIR/SHA256SUMS" --status && echo "BACKUP INTACT"
```

- Sukces: `SETTINGS MATCH`, settings w panelu odpowiadają stanowi sprzed sesji, WiFi połączone.
- Stop: rozbieżność — odnotować jako `needs_recovery`, zachować `$BACKUP_DIR` i logi; nie usuwać backupu po nieudanym rollbacku.

Uwaga o plikach, których ten rollback **nie odtwarza automatycznie**: stan usług `iwd`/`wpa_supplicant`, helper `99-valve-wifi-backend.conf` (celowo nietknięty), efekty `autoconnect-priority 100` pozostawione przez starą wersję (usunięcie tylko przez forget/rejoin profilu albo przyszłą jawną naprawę), efekty dyspozytora zastosowane między deployem a rollbackiem. Każdą z tych rozbieżności odnotować w wyniku sesji zamiast twierdzić, że „wróciło do baseline".

## 5. Czego NIGDY nie robić zdalnie

1. **Reset / Uninstall jako „mechanizm recovery".** Aktualne ścieżki reset/uninstall piszą assumed defaults (i usuwają pliki backend/dispatcher), nie odtwarzają stanu sprzed sesji. Reset dodatkowo rekonsyluje do `power_save_disabled=True` + `auto_fix_on_wake=True` — to nie jest „wyłączenie wszystkiego".
2. **Backend switch bez lokalnego dostępu zapasowego.** Przełączenie backendu dotyka konfiguracji systemowej i stanu usług; przerwane w połowie odcina zdalny dostęp. Tylko za osobną zgodą, z planem powrotu i fizycznym dostępem do Decka.
3. **Kasowanie profili / zmiana kanału OS „dla czystego baseline".** Zakazane protokołem: nie usuwamy wszystkich profili ani nie zmieniamy kanału SteamOS żeby uzyskać ładny pomiar.
4. **Publikowanie Stable-release jako fixture testowego.** Ścieżki update testujemy na kontrolowanych kandydatach; nie tworzymy placeholder-Stable.
5. **„Naprawianie" uprawnień/polityki sudo,.Firewall/SSH na siłę.** Odmowa `sudo -n` z 2026-10-16 to dana, nie przeszkoda do obejścia. Bez zmian haseł i polityki.
6. **Zostawianie CEF remote debugging / React DevTools włączonych po sesji.** Tylko zaufana sieć, wyłączyć po zakończeniu.
7. **Commitowanie surowych raportów/backupów.** `$BACKUP_DIR` i `$OUT_DIR` nigdy nie trafiają do repo (nawet `out/` jest lokalne i ignorowane); do `docs/testing/results/` tylko przejrzany, oczyszczony wynik bez sekretów i identyfikatorów sieci.
8. **Powtarzanie mutacji po nieznanym stanie.** Timeout/utrata połączenia w trakcie zapisu = stan nieznany → §4, nie retry tego samego zapisu.
9. **Traktowanie ZIP-reinstall jako pristine baseline.** Natywny replace w Decky 3.2.10 może wywołać `_uninstall()` z obecnymi niebezpiecznymi założeniami; reinstall nie dowodzi bezpiecznego update ani nie czyści historii systemowej.
10. **Odtwarzanie cudzych profili z backupu.** Przywracamy wyłącznie pliki własne wtyczki i własne settings; profili sąsiadów / nie-własnych kluczy nie dotykamy (owned-only).

## 6. SSH transport i owner-sudo (wiedza sesyjna 2026-10-17)

Ustalenia z sesji walidacyjnych 2026-10-17, obowiązujące dopóki urządzenie/wirtualka nie ulegnie zmianie. Nie obejmują autoryzacji mutacji (§0) — opisują tylko **jak** wykonywać czynności read-only i przekazywać ownerowi kroki sudo.

### Transport SSH (agent → Deck, read-only)

- Zestaw potwierdzony w tej sesji (2026-10-17): selektor tożsamości
  $env:TEMP\steamdeck-agent-7.pub oraz plik znanych hostów
  $env:TEMP\steamdeck-knownhosts-verified. To ścieżki tymczasowe PC, nie
  repozytorium; w nowej sesji mogą nie istnieć. Nie zakładać, że
  steamdeck-id.pub wskazuje tę samą tożsamość.
- Opcja -i wskazuje tożsamość klienta: plik klucza prywatnego albo publiczny
  selektor pasujący do klucza prywatnego załadowanego w ssh-agent. Nie podawać
  klucza serwera Decka (/etc/ssh/ssh_host_ed25519_key.pub). Plik
  /home/deck/.ssh/authorized_keys na Decku zawiera dozwolone klucze publiczne
  klientów; jego fingerprint sam nie dowodzi, że PC wybrał odpowiadającą mu
  tożsamość prywatną.
- W poprzedniej próbie Deck odpowiadał: sshd był aktywny i nasłuchiwał na
  porcie 22, a log pokazywał połączenia resetowane podczas publickey preauth.
  To wskazywało na nieudaną autoryzację klienta, nie na wyłączony serwer SSH.
  Sam wpis w authorized_keys nie potwierdza, którego klucza użył klient.
- Szablon PowerShell dla odczytu:

      $deckIdentity = Join-Path $env:TEMP 'steamdeck-agent-7.pub'
      $deckKnownHosts = Join-Path $env:TEMP 'steamdeck-knownhosts-verified'
      ssh -o BatchMode=yes -o ConnectTimeout=30 -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes "-o UserKnownHostsFile=$deckKnownHosts" -i $deckIdentity deck@steamdeck '<cmd>'

- IdentitiesOnly=yes jest osobną regułą: przy ≥6 kluczach w agencie SSH
  odrzuca logowanie "Too many authentication failures" bez tego przełącznika.
  StrictHostKeyChecking=yes wymaga wcześniejszego zweryfikowania host key;
  nie wyłączaj kontroli i nie ufaj automatycznie nowemu kluczowi.
- Jeśli potwierdzony selektor lub plik known_hosts nie istnieje, użyć
  tożsamości skonfigurowanej przez właściciela i zweryfikowanego pliku
  known_hosts. Agent nie otwiera ani nie odczytuje sejfu Bitwarden. Host
  steamdeck rozwiązuje się na PC operatora (DNS/hosts); sshd na Decku włącza
  owner w Panelu sterowania na życzenie.
- Złożone komendy i bloki skryptowe: nie cytować inline przez PowerShell
  (PowerShell zniekształca cudzysłowy i $). Wykonać: zapis do pliku → scp na
  Deck (/tmp/<script>.sh) → ssh … 'bash /tmp/<script>.sh'. Pliki tymczasowe na
  PC operatora trzymać w $env:TEMP\opencode\.

### Sudo — podział ról

- `sudo -v` wpisane w Konsole na Decku **nie upoważnia sesji SSH** (tty
  tickets). Hasło właściciela jest wpisowane wyłącznie przez ownera; nigdy nie
  jest przesyłane agentowi.
- Każde mutujące wywołanie sudo wykonuje **właściciel** w Konsole, z bloku komend
  przygotowanego przez agenta (wzory: §1 krok 1.1, §2 backup, §3 deploy).
  Agent po stronie SSH może zweryfikować wynik read-only (`systemctl
  is-active plugin_loader`, `ls -l`, `sha256sum -c`, `cat` plików w
  `$BACKUP_DIR`), ale nie uruchamia `sudo`.
- Bloki sudo ownera są tak skonstruowane, aby porażka była widoczna: `set -e`,
  `sha256sum -c` jako bramka przed `cp`, `is-active` na końcu. Porażka bloku =
  stan **nieznany**, nie sukces (§0 stop rule).

### Deploy kandydata — ustalona praktyka (ścieżka B, staging)

Do czasu pierwszej publikacji na GitHubie `install.sh` nie ma przypiętego
artefaktu kandydata: spada wtedy na gałąź `main` (AGENTS.md), która nie jest
immutabilnym kandydatem — dlatego deploy kandydata robi się stagingiem
(poniżej), nie `install.sh`. Stosowana procedura:

1. Agent buduje kandydata lokalnie (Git Bash/CMD: `pnpm install --frozen-lockfile`,
   `pnpm run build` dla każdego wdrożenia pakietu) i przygotowuje katalog
   staging na PC oraz zestaw plików: `plugin.json`, `package.json`,
   `main.py`, `decky.pyi`, `dist/index.js`, `dist/index.js.map`,
   `defaults/dispatcher.sh.tmpl`.
2. Agent wgrywa staging na Deck (`scp` do `~/candidate-stage<NN>`) i zdalnie
   wypisuje listę `sha256sum` plików; owner w Konsole uruchamia blok: `sudo cp -p`
   wymienionych plików do `$PLUGIN_DIR` (utworzyć
   katalogi), `sha256sum -c` **FILES-OK** w stagingu vs `$PLUGIN_DIR`,
   `sudo systemctl restart plugin_loader`,
   `systemctl is-active plugin_loader`.

Stop: jeśli `FILES-OK` się nie zgadza albo `is-active` nie wraca, przejść do
§4 (rollback), nie powtarzać bloku ponownie (§0 stop rule).

### Backupy istniejące na urządzeniu (zapisane, nie tworzyć ponownie)

- `~/wifi-optimizer-recovery-20261017-1154` — backup pre-mutacyjny: `settings.json`,
  `last_enforced`, 4 systemowe conf-y (NM/modprobe + REFONLY: valve/dispatcher),
  2 wykazy `nmcli`; manifest `SHA256SUMS` zweryfikowany (`sha256sum -c`).
  Układ płaski, bez podkatalogu `etc/` (wg S-PRE-02); przy najbliższym kontakcie
  z urządzeniem potwierdzić odczytem `ls`, przy rozbieżności poprawić ten punkt
  + §4.
- `~/wifi-optimizer-recovery-20261017-1243` — refresh po testach; zawiera
  `system_snapshot.json` (tworzony przez poprawioną ścieżkę kodu).

Zmiana ustawień/systemu bez ich ochrony jest §0-naruszeniem; przy
odtwarzaniu z backupu, aktualizować datowane wpisy w matrycy walidacyjnej.

## Załącznik: minimalna ściąga komend (sukces vs stop)

| # | Komenda (pełne ścieżki) | Sukces | Stop |
| --- | --- | --- | --- |
| I1 | `sudo /usr/bin/python3 ./collect_deck_inventory.py --decky-home "$HOME/homebrew" > "$HOME/wifi-optimizer-inventory.json"` | exit `0`, JSON niepusty | nie-0 / pusty plik → zachować błąd, nie zmieniać konfiguracji |
| I2 | `cat ~/homebrew/plugins/WiFi\ Optimizer/plugin.json` | nazwa/wersja kandydata | brak katalogu → nie instalować bez zgody |
| I3 | `systemctl --no-pager status plugin_loader.service` | `active (running)` | `inactive/failed` → nie startować bez zgody |
| B1 | `/bin/cp -p ~/homebrew/settings/WiFi\ Optimizer/settings.json $BACKUP_DIR/` | kopia + wpis SHA256 | brak źródła → przerwać, zweryfikować `$SETTINGS_DIR` |
| B2 | `sha256sum -c $BACKUP_DIR/SHA256SUMS` | wszystkie `OK` | jakikolwiek `FAILED` → nie deployować |
| D1 | `bash ./install.sh` | czysty log, nowa wersja w `plugin.json` | błąd poboru/manifestu/miejsca → §4 |
| D2 | `sudo /usr/bin/systemctl restart plugin_loader` + `systemctl --no-pager is-active plugin_loader.service` | `active` w ~30 s | brak powrotu → journal (odczyt) + §4 |
| V1 | `sha256sum ~/homebrew/plugins/WiFi\ Optimizer/dist/index.js` | zgodny z artefaktem kandydata | niezgodny → `load_unconfirmed`, nie testować dalej |
| R1 | sekwencja stop → cp z `$BACKUP_DIR` → start (§4) | `SETTINGS MATCH`, WiFi połączone | rozbieżność → `needs_recovery`, zachować backup i logi |
