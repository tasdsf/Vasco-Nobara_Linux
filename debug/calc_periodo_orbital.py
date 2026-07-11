import math
G = 6.674e-11
M_terra = 5.972e24
M_fujin5 = 0.913 * M_terra  # kg
r_estacao = 6_969_000       # metros (raio + altitude 1000km)
T = 2 * math.pi * math.sqrt(r_estacao**3 / (G * M_fujin5))
print(f"Período orbital estação: {T:.0f}s = {T/3600:.2f}h")
r_carrier = 10_000_000
T2 = 2 * math.pi * math.sqrt(r_carrier**3 / (G * M_fujin5))
print(f"Período orbital carrier: {T2:.0f}s = {T2/3600:.2f}h")
