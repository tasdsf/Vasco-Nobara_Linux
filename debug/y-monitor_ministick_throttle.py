"""
Monitor ao vivo do mini-joystick analógico do TWCS Throttle (ABS_X / ABS_Y),
que é o candidato a ficar descentrado e injetar yaw indesejado no jogo.

Uso: python debug/monitor_ministick_throttle.py
Sai com CTRL+C.
"""
import evdev
import selectors

CAMINHO_DISPOSITIVO = "/dev/input/by-id/usb-Thrustmaster_TWCS_Throttle-event-joystick"

EIXOS_MONITORIZADOS = {
    evdev.ecodes.ABS_X: "MINISTICK X",
    evdev.ecodes.ABS_Y: "MINISTICK Y",
    evdev.ecodes.ABS_RZ: "RZ (rotativo base)",
}

dev = evdev.InputDevice(CAMINHO_DISPOSITIVO)
print(f"[SISTEMA] A monitorizar: {dev.name} ({CAMINHO_DISPOSITIVO})")
print("[SISTEMA] CTRL+C para sair.\n")

info_eixos = {}
for codigo in EIXOS_MONITORIZADOS:
    absinfo = dev.absinfo(codigo)
    centro = (absinfo.min + absinfo.max) / 2
    info_eixos[codigo] = {"min": absinfo.min, "max": absinfo.max, "centro": centro, "flat": absinfo.flat}
    print(f"[CALIBRACAO] {EIXOS_MONITORIZADOS[codigo]}: min={absinfo.min} max={absinfo.max} "
          f"centro_teorico={centro:.0f} zona_morta=±{absinfo.flat}")

print("\n[VALORES INICIAIS]")
for codigo, nome in EIXOS_MONITORIZADOS.items():
    valor = dev.absinfo(codigo).value
    desvio = valor - info_eixos[codigo]["centro"]
    print(f"  {nome}: {valor}  (desvio do centro: {desvio:+.0f})")

print("\n[SISTEMA] A aguardar movimento... (só imprime quando o valor muda)\n")

try:
    for evento in dev.read_loop():
        if evento.type == evdev.ecodes.EV_ABS and evento.code in EIXOS_MONITORIZADOS:
            nome = EIXOS_MONITORIZADOS[evento.code]
            centro = info_eixos[evento.code]["centro"]
            flat = info_eixos[evento.code]["flat"]
            desvio = evento.value - centro
            fora_da_zona_morta = abs(desvio) > flat
            marca = " <-- FORA DA ZONA MORTA" if fora_da_zona_morta else ""
            print(f"[{nome}] valor={evento.value}  desvio={desvio:+.0f}{marca}")
except KeyboardInterrupt:
    print("\n[SISTEMA] Encerrado.")
