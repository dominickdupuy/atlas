# Pi provisioning

Ansible playbooks are deliberately deferred until the Pi exists and the manual
process has been done once — automating a process you have never run by hand
is how provisioning scripts rot. Until then, this is the checklist the
playbook will eventually encode:

1. Flash Raspberry Pi OS (Bookworm, 64-bit, desktop for the kiosk session) to
   the SSD; boot from it (D13).
2. Static IP via DHCP reservation on the router; prefer the 5GHz band (D15).
3. `sudo apt install docker.io docker-compose-plugin cage chromium-browser swayidle wlopm`
4. Join the tailnet: `sudo tailscale up` (D12).
5. Clone this repo to `/home/pi/atlas`; `cp .env.example .env` and fill in;
   set the runner and ntfy port mappings in `compose.yaml` to the Tailscale
   interface address.
6. Install the units from `infra/systemd/` (each file's header has its own
   install steps): `wifi-powersave-off`, `atlas-compose`, `atlas-kiosk`.
7. `docker compose --profile ha up -d`; finish HA onboarding in the browser;
   configure MQTT, recorder exclusions are already in config; add the MCP
   Server integration (D4).
8. Set up restic backups for the data volumes; backups exclude or encrypt
   secrets (section 8).
