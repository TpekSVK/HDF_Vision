#!/usr/bin/env bash
# Run on the Jetson host as the desktop user, not inside Docker or via sudo.
set -euo pipefail
umask 077

usage() {
  cat <<'EOF'
Použitie: bash tools/setup-desktop.sh [--restore | --help]

Bez argumentov: zapne Nerušiť a potlačí upozornenia na aktualizácie.
--restore: obnoví nastavenia uložené pred prvým spustením.
Spustite v termináli pracovnej plochy Jetsonu, bez sudo a mimo Dockeru.
EOF
}
die() { echo "CHYBA: $*" >&2; exit 1; }
mode="${1:-apply}"
[[ $# -le 1 ]] || { usage; exit 2; }
case "$mode" in
  --help|-h) usage; exit 0 ;;
  apply|--restore) ;;
  *) usage; exit 2 ;;
esac
[[ $EUID -ne 0 ]] || die "Spustite skript ako používateľ pracovnej plochy, bez sudo."
[[ -n ${DBUS_SESSION_BUS_ADDRESS:-} ]] || die "Otvorte terminál v prihlásenej pracovnej ploche Jetsonu."
for command in gsettings dconf flock; do
  command -v "$command" >/dev/null || die "Chýba príkaz $command."
done
[[ ${GSETTINGS_BACKEND:-dconf} == dconf ]] || die "Vyžaduje sa trvalé úložisko dconf."

config_dir="${XDG_CONFIG_HOME:-$HOME/.config}"
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/hdf-vision/desktop"
autostart="$config_dir/autostart/update-notifier.desktop"
backup="$state_dir/original"
mkdir -p "$state_dir"
exec 9>"$state_dir/lock"
flock -n 9 || die "Iné spustenie skriptu ešte pracuje."

# Store only the keys we change; an empty dconf value means no user override.
schemas=(org.gnome.desktop.notifications com.ubuntu.update-notifier org.gnome.software)
keys=(show-banners no-show-notifications download-updates-notify)
paths=(/org/gnome/desktop/notifications/show-banners /com/ubuntu/update-notifier/no-show-notifications /org/gnome/software/download-updates-notify)
values=(false true false)

if [[ "$mode" == --restore && ! -d "$backup" ]]; then
  echo "Nie je uložená záloha; nič sa nezmenilo."
  exit 0
fi

# Check availability and policy locks before changing anything.
for i in "${!keys[@]}"; do
  gsettings get "${schemas[i]}" "${keys[i]}" >/dev/null || die "Chýba nastavenie ${schemas[i]}/${keys[i]}."
  [[ $(gsettings writable "${schemas[i]}" "${keys[i]}") == true ]] || die "Nastavenie ${keys[i]} je uzamknuté správcom."
done

if [[ "$mode" == apply && ! -d "$backup" ]]; then
  temporary=$(mktemp -d "$state_dir/.backup.XXXXXX")
  trap 'rm -rf -- "$temporary"' EXIT
  for i in "${!keys[@]}"; do
    dconf read "${paths[i]}" > "$temporary/key-$i"
  done
  if [[ -e "$autostart" || -L "$autostart" ]]; then
    [[ -f "$autostart" ]] || die "$autostart nie je bežný súbor."
    cp -a -- "$autostart" "$temporary/update-notifier.desktop"
  else
    touch "$temporary/autostart-absent"
  fi
  mv -- "$temporary" "$backup"
  trap - EXIT
fi

[[ -d "$backup" ]] || die "Chýba záloha pôvodných nastavení."
for i in "${!keys[@]}"; do
  [[ -f "$backup/key-$i" ]] || die "Záloha je neúplná: key-$i."
done
[[ -f "$backup/autostart-absent" || -e "$backup/update-notifier.desktop" || -L "$backup/update-notifier.desktop" ]] || die "Záloha automatického spúšťania je neúplná."

# Keep the backup on failure, so --restore can recover a partial application.
trap 'echo "Nastavenie sa nedokončilo. Záloha zostala zachovaná; skúste --restore." >&2' ERR
mkdir -p "$(dirname "$autostart")"
if [[ "$mode" == --restore ]]; then
  for i in "${!keys[@]}"; do
    original=$(cat "$backup/key-$i")
    if [[ -n "$original" ]]; then
      dconf write "${paths[i]}" "$original"
    else
      dconf reset "${paths[i]}"
    fi
    [[ $(dconf read "${paths[i]}") == "$original" ]] || die "Obnova ${keys[i]} sa nepodarila; záloha zostala zachovaná."
  done
  rm -f -- "$autostart"
  if [[ ! -f "$backup/autostart-absent" ]]; then
    cp -a -- "$backup/update-notifier.desktop" "$autostart"
  fi
  rm -rf -- "$backup"
  echo "Pôvodné nastavenia boli obnovené. Odhláste sa a znova prihláste."
else
  for i in "${!keys[@]}"; do
    gsettings set "${schemas[i]}" "${keys[i]}" "${values[i]}"
    [[ $(gsettings get "${schemas[i]}" "${keys[i]}") == "${values[i]}" ]] || die "Zápis ${keys[i]} sa nepodaril; záloha zostala zachovaná."
  done
  # A user override suppresses the system autostart without modifying /etc.
  # Replace atomically, including when the original file was a symlink.
  temporary=$(mktemp "$(dirname "$autostart")/.hdf-notifier.XXXXXX")
  trap 'rm -f -- "$temporary"' EXIT
  cat > "$temporary" <<'EOF'
[Desktop Entry]
Type=Application
Name=Update Notifier
Hidden=true
EOF
  mv -fT -- "$temporary" "$autostart"
  trap - EXIT
  echo "Režim Nerušiť je zapnutý. Upozornenia na aktualizácie sú potlačené."
  echo "Odhláste sa a znova prihláste (alebo reštartujte), aby sa zastavil aj už bežiaci oznamovač."
  echo "Samotné aktualizácie sa nemenia. Už otvorené okná sa automaticky nezatvárajú."
  echo "Záloha pôvodných nastavení: $backup"
fi
