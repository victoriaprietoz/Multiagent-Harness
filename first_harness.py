"""Ejemplo educativo de harness multiagente que usa Codex CLI con ChatGPT.

Requisitos: Python 3.10+, Codex CLI instalado, `codex login` o `codex` autenticado.
Ejecuta: py harness_codex_4_agentes.py (PowerShell, Windows 10)
No necesita paquetes pip ni una clave API de OpenAI.

Cada llamada `codex exec` es independiente. El estado se comparte desde Python,
no existe memoria automática entre esos procesos.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

CARPETA = Path(__file__).resolve().parent / "demo_harness_codex"
CARPETA.mkdir(exist_ok=True)
MAX_PASOS = 8
TIMEOUT_CODEX = 240

# Ejercicio deliberadamente pequeño y tests controlados por el harness.
OBJETIVO = (
    "Crear calculadora.py con sumar(a, b) y dividir(a, b). "
    "La división entre cero debe lanzar ValueError. No uses dependencias externas."
)
TESTS = '''import unittest
from calculadora import sumar, dividir

class CalculadoraTests(unittest.TestCase):
    def test_suma(self):
        self.assertEqual(sumar(2, 3), 5)
        self.assertEqual(sumar(-2, 1), -1)

    def test_division(self):
        self.assertEqual(dividir(8, 2), 4)

    def test_cero(self):
        with self.assertRaises(ValueError):
            dividir(1, 0)

if __name__ == "__main__":
    unittest.main()
'''
(CARPETA / "test_calculadora.py").write_text(TESTS, encoding="utf-8")

ESQUEMA = {
    "type": "object",
    "properties": {
        "agente": {
            "type": "string",
            "enum": ["planificador", "programador", "tester", "corrector", "terminar"],
        },
        "motivo": {"type": "string"},
    },
    "required": ["agente", "motivo"],
    "additionalProperties": False,
}
(CARPETA / "decision.schema.json").write_text(
    json.dumps(ESQUEMA, ensure_ascii=False, indent=2), encoding="utf-8"
)


def llamar_codex(prompt, permiso="read-only", json_estructurado=False):
    """Llama a Codex como proceso, sin SDK ni clave de API independiente."""
    ejecutable = shutil.which("codex")
    if not ejecutable:
        raise RuntimeError("No encuentro 'codex' en PATH. Instala Codex CLI y entra en tu cuenta.")
    comando = [
        ejecutable, "exec", "--skip-git-repo-check",
        "--sandbox", permiso,
    ]
    if json_estructurado:
        comando += ["--output-schema", str(CARPETA / "decision.schema.json")]
    comando += ["-"]  # Lee el prompt desde stdin, sin problemas de comillas en Windows.

    try:
        resultado = subprocess.run(
            comando, input=prompt, cwd=CARPETA,
            text=True, encoding="utf-8", errors="replace",
            capture_output=True, timeout=TIMEOUT_CODEX, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Codex agotó el límite de tiempo por llamada.") from exc
    if resultado.returncode != 0:
        raise RuntimeError(
            f"Codex terminó con error {resultado.returncode}:\n{resultado.stderr[-2500:]}"
        )
    salida = resultado.stdout.strip()
    if not salida:
        raise RuntimeError("Codex no devolvió una respuesta final.")
    return salida


def decidir(estado):
    """El coordinador usa Codex para elegir dinámicamente el siguiente agente."""
    instrucciones = f"""
Eres el COORDINADOR de cuatro agentes. Nunca escribas archivos.
Objetivo: {OBJETIVO}
Estado actual: {json.dumps(estado, ensure_ascii=False)}
Elige UNA acción:
- planificador: si merece la pena diseñar primero el plan (una sola vez).
- programador: si falta implementar calculadora.py.
- tester: si hay código nuevo o corregido pendiente de probar.
- corrector: si han fallado los tests.
- terminar: solo si pruebas_ok=true.
Prefiere la acción más útil; puedes saltarte planificador.
Responde estrictamente según el esquema JSON proporcionado.
"""
    decision = json.loads(llamar_codex(instrucciones, json_estructurado=True))
    return decision["agente"], decision["motivo"]


def ejecutar_tests():
    """La validación la ejecuta Python: no dependemos de la opinión de un LLM."""
    resultado = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-p", "test_calculadora.py", "-v"],
        cwd=CARPETA, capture_output=True, text=True, timeout=30, check=False,
    )
    registro = (resultado.stdout + "\n" + resultado.stderr)[-3500:]
    return resultado.returncode == 0, registro


def ejecutar_agente(nombre, estado):
    """Cuatro roles. Los permisos son del proceso Codex, no de un modelo diferente."""
    comun = f"Objetivo: {OBJETIVO}\nPlan: {estado['plan']}\n"
    if nombre == "planificador":
        estado["plan"] = llamar_codex(
            comun + "Eres PLANIFICADOR. Escribe un plan conciso. No edites archivos.",
            permiso="read-only",
        )[:1500]
        print("  Plan:", estado["plan"][:180])

    elif nombre == "programador":
        print("  Codex va a crear calculadora.py en la carpeta de demostración.")
        llamar_codex(
            comun + "Eres PROGRAMADOR. Implementa calculadora.py en esta carpeta. "
            "No cambies test_calculadora.py ni decision.schema.json. "
            "Trabaja únicamente en esta carpeta. No instales dependencias.",
            permiso="workspace-write",
        )
        estado["pruebas_ejecutadas"] = False
        estado["pruebas_ok"] = False

    elif nombre == "tester":
        # El revisor puede inspeccionar, pero no modificar archivos.
        revision = llamar_codex(
            comun + "Eres TESTER. Lee calculadora.py y test_calculadora.py. "
            "Señala posibles defectos brevemente. No modifiques archivos.",
            permiso="read-only",
        )
        ok, errores = ejecutar_tests()
        estado["pruebas_ejecutadas"] = True
        estado["pruebas_ok"] = ok
        estado["errores"] = "" if ok else errores
        print("  Tests:", "SUPERADOS" if ok else "FALLIDOS")
        print("  Revisión:", revision[:180].replace("\n", " "))
        if not ok:
            print("  Error:", errores[-450:])

    elif nombre == "corrector":
        llamar_codex(
            comun + "Eres CORRECTOR. Repara calculadora.py para superar estos tests:\n"
            + estado["errores"] + "\nNo edites los tests ni otros archivos.",
            permiso="workspace-write",
        )
        estado["pruebas_ejecutadas"] = False
        estado["pruebas_ok"] = False

    # El harness restaura siempre las pruebas controladas si alguien las modificó.
    archivo_tests = CARPETA / "test_calculadora.py"
    if archivo_tests.read_text(encoding="utf-8") != TESTS:
        archivo_tests.write_text(TESTS, encoding="utf-8")
        print("  Aviso: se restauraron los tests originales.")


def proteger_decision(agente, estado):
    """La IA propone; las reglas duras las aplica Python."""
    existe_codigo = (CARPETA / "calculadora.py").exists()
    if estado["pruebas_ok"]:
        return "terminar"  # Nunca repetir tras superar las pruebas.
    if agente == "terminar":
        return "tester" if existe_codigo else "programador"
    if agente == "planificador" and estado["plan"]:
        return "tester" if existe_codigo else "programador"
    if agente in ("tester", "corrector") and not existe_codigo:
        return "programador"
    if agente == "corrector" and not (estado["pruebas_ejecutadas"] and not estado["pruebas_ok"]):
        return "tester" if existe_codigo else "programador"
    if agente == "programador" and existe_codigo:
        return "tester"
    return agente


def main():
    estado = {
        "plan": "",
        "pruebas_ejecutadas": False,
        "pruebas_ok": False,
        "errores": "",
        "historial": [],
    }
    print("Carpeta de trabajo:", CARPETA)
    print("El harness puede ejecutar hasta", MAX_PASOS, "agentes.\n")
    for paso in range(1, MAX_PASOS + 1):
        propuesta, motivo = decidir(estado)
        agente = proteger_decision(propuesta, estado)
        print(f"Paso {paso}: coordinador propone {propuesta} ({motivo})")
        if agente != propuesta:
            print(f"  Regla del harness: ejecutar {agente} en su lugar.")
        if agente == "terminar":
            print("\nÉXITO: tests superados. Archivo:", CARPETA / "calculadora.py")
            return
        ejecutar_agente(agente, estado)
        estado["historial"].append(agente)
        estado["historial"] = estado["historial"][-8:]

    if estado["pruebas_ok"]:
        print("\nÉXITO: las pruebas pasan. Archivo:", CARPETA / "calculadora.py")
    else:
        print("\nDETENIDO: límite de pasos alcanzado sin pruebas superadas.")
        print("Revisa:", CARPETA)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print("\nERROR:", exc, file=sys.stderr)
        sys.exit(1)
