#!/usr/bin/env python3
"""
los_calibrar.py — Registo interativo de observações para o LOS Checker.
Corre este script quando tiveres informação visual do jogo sobre o estado
actual do carrier (oclusos ou visível). Grava na base de dados Postgres
partilhada, associado ao sistema atual (detetado pelo journal) -- ver
los_checker.py para detalhes de ligação (variável R2D2_DB_PASSWORD).
"""

from infra_bridge import ED_LOG_DIR
from los_checker import obter_sistema_atual, registar_observacao, _alertar_falha_bd


def main():
    sistema = obter_sistema_atual(ED_LOG_DIR)
    if not sistema:
        print("[ERRO] Não foi possível determinar o sistema atual pelo journal.")
        return

    print("=" * 50)
    print(f"  LOS CALIBRADOR — Sistema {sistema}")
    print("=" * 50)
    print()
    print("Estado actual do carrier no jogo:")
    print("  1 - Oclusos (carrier atrás do planeta, tracejado)")
    print("  2 - Visível (linha directa livre)")
    print()

    escolha = input("Estado actual (1/2): ").strip()

    if escolha == "1":
        estado = "oclusos"
    elif escolha == "2":
        estado = "visivel"
    else:
        print("Opção inválida.")
        return

    nota = input("Nota (opcional): ").strip()

    try:
        registar_observacao(sistema, estado, nota, origem="linux")
    except Exception as e:
        _alertar_falha_bd(e)
        return

    print()
    print("Agora corre: python3 los_checker.py")


if __name__ == "__main__":
    main()
