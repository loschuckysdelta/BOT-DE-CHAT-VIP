import asyncio
import json
import logging
import os
import re
import sys
import time
import unicodedata
import urllib.request
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

# ================== CONFIGURACION ==================
API_ID = 34501633
API_HASH = "cc60b8ad4b4e95ab0908e6ef379efc49"

BASE = Path(__file__).resolve().parent
SESION = BASE / "loschuckys_sesion"
QR_URL = "https://i.postimg.cc/sfccGwZ0/image.png"
QR_ARCHIVO = BASE / "qr_pago.png"
IMAGEN_ASESOR = BASE / "asesor.png"  # Opcional
NUMERO_CUENTA = "09400141002543052450"
ESTADO_ARCHIVO = BASE / "estado_vip.json"
VIGENCIA = 7 * 24 * 60 * 60
RECONEXION_SEGUNDOS = 10

# Para cambiar las tarifas en el futuro, modifica solo estos 4 valores.
PLANES = {
    "7": {"nombre": "7 DÍAS", "precio": 5},
    "30": {"nombre": "30 DÍAS", "precio": 20},
    "60": {"nombre": "2 MESES (60 DÍAS)", "precio": 35},
    "permanente": {"nombre": "PAGO ÚNICO (PERMANENTE)", "precio": 50},
}
OPCIONES = {"1": "7", "2": "30", "3": "60", "4": "permanente"}

logger = logging.getLogger("loschuckys.vip")
logger.setLevel(logging.INFO)
if not logger.handlers:
    formato = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(formato)
    archivo_log = RotatingFileHandler(
        BASE / "bot_vip.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8"
    )
    archivo_log.setFormatter(formato)
    logger.addHandler(consola)
    logger.addHandler(archivo_log)

client = TelegramClient(
    str(SESION), API_ID, API_HASH,
    auto_reconnect=True,
    connection_retries=10,
    retry_delay=5,
    request_retries=5,
    timeout=20,
)

# Guarda estado por usuario. Para cada chat hay un bloqueo distinto.
estados = {}
bloqueos = {}


def cargar_estado():
    if not ESTADO_ARCHIVO.exists():
        return {}
    try:
        datos = json.loads(ESTADO_ARCHIVO.read_text(encoding="utf-8"))
        if not isinstance(datos, dict):
            return {}
        resultado = {}
        ahora = time.time()
        for uid, valor in datos.items():
            # Compatible con el archivo viejo {"123": 1760000000.0}
            if isinstance(valor, (int, float)):
                valor = {"fecha": float(valor), "estado": "pendiente"}
            if not isinstance(valor, dict):
                continue
            fecha = float(valor.get("fecha", 0))
            if not (0 <= ahora - fecha < VIGENCIA):
                continue
            estado = valor.get("estado")
            if estado not in ("pendiente", "revision"):
                continue
            plan = valor.get("plan")
            if plan not in PLANES:
                plan = None
            resultado[str(uid)] = {"fecha": fecha, "estado": estado, "plan": plan}
        return resultado
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("No se pudo leer el estado anterior: %s", exc)
        return {}


def guardar_estado():
    try:
        temporal = ESTADO_ARCHIVO.with_suffix(".tmp")
        temporal.write_text(
            json.dumps(estados, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        temporal.replace(ESTADO_ARCHIVO)
    except OSError:
        logger.exception("No se pudo guardar estado_vip.json")


def estado_actual(uid):
    dato = estados.get(uid)
    if dato and time.time() - dato["fecha"] >= VIGENCIA:
        estados.pop(uid, None)
        guardar_estado()
        return None
    return dato


def fijar_estado(uid, nuevo, plan=None):
    estados[uid] = {"fecha": time.time(), "estado": nuevo, "plan": plan}
    guardar_estado()


def normalizar(texto):
    texto = unicodedata.normalize("NFD", texto.lower())
    texto = "".join(
        letra for letra in texto if unicodedata.category(letra) != "Mn"
    )
    return re.sub(r"\s+", " ", texto).strip()


def detectar_plan(texto):
    """Reconoce nombre, duración, opción numérica o precio explícito."""
    t = normalizar(texto)
    if t in OPCIONES:
        return OPCIONES[t]
    patrones = (
        ("permanente", r"\b(permanente|pago\s+unico|para\s+siempre|de\s+por\s+vida)\b"),
        ("60", r"\b(2\s*meses|dos\s+meses|60\s*dias)\b"),
        ("30", r"\b(30\s*dias|1\s*mes|un\s+mes|mensual)\b"),
        ("7", r"\b(7\s*dias|una\s+semana|1\s*semana)\b"),
    )
    for codigo, patron in patrones:
        if re.search(patron, t):
            return codigo
    # Menciones por precio: "S/ 5", "20 soles", "35 sol".
    for codigo, datos in PLANES.items():
        precio = datos["precio"]
        if re.search(rf"\bs\s*/\s*{precio}\b|\b{precio}\s*(?:soles|sol)\b", t):
            return codigo
    return None


def pide_menu(texto):
    t = normalizar(texto)
    return bool(re.search(
        r"(^|\s)(/vip|vip|/start|planes?|precios?|tarifas?|costos?|menu)(\s|$)", t
    ) or "cuanto cuesta" in t or "cuanto cuestan" in t or "que planes" in t)


def es_imagen(event):
    if event.message.photo:
        return True
    archivo = event.message.file
    return bool(archivo and (archivo.mime_type or "").startswith("image/"))


def texto_planes():
    return (
        "👑 <b>LOS CHUCKYS — PLANES VIP</b>\n\n"
        "1️⃣ <b>7 DÍAS</b> — S/ 5\n"
        "2️⃣ <b>30 DÍAS</b> — S/ 20 🔥 MÁS VENDIDO\n"
        "3️⃣ <b>2 MESES (60 DÍAS)</b> — S/ 35\n"
        "4️⃣ <b>PAGO ÚNICO</b> — S/ 50 💎 PERMANENTE\n\n"
        "✍️ Para comprar, responde <b>1, 2, 3 o 4</b>, o escribe:\n"
        "<i>Quiero comprar el plan VIP de 7 días</i>\n\n"
        "💳 Te enviaré el QR y los datos bancarios por separado."
    )


async def mandar_menu(event):
    await event.respond(texto_planes(), parse_mode="html")


def preparar_qr():
    if QR_ARCHIVO.is_file() and QR_ARCHIVO.stat().st_size > 0:
        logger.info("QR listo (archivo local)")
        return
    try:
        solicitud = urllib.request.Request(
            QR_URL, headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(solicitud, timeout=15) as respuesta:
            contenido = respuesta.read()
        if not contenido:
            raise ValueError("La descarga del QR llego vacia")
        QR_ARCHIVO.write_bytes(contenido)
        logger.info("QR descargado correctamente")
    except Exception as exc:
        logger.warning("No se pudo descargar el QR: %s", exc)


async def mandar_qr(event):
    # 1er mensaje: solo foto del QR, sin caption.
    if QR_ARCHIVO.is_file() and QR_ARCHIVO.stat().st_size > 0:
        try:
            await event.respond(file=str(QR_ARCHIVO))
            logger.info("QR enviado al usuario %s", event.sender_id)
            return
        except FloodWaitError:
            raise
        except Exception as exc:
            logger.warning("Fallo foto QR a %s: %s", event.sender_id, exc)
    await event.respond("📲 QR de pago:\n" + QR_URL)


async def mandar_cuenta(event, plan=None):
    # 2do mensaje: selección y cuenta bancaria, separado del QR.
    if plan in PLANES:
        datos = PLANES[plan]
        resumen = (
            f"📦 <b>Plan solicitado:</b> {datos['nombre']}\n"
            f"💰 <b>Total a pagar:</b> S/ {datos['precio']}\n\n"
        )
    else:
        resumen = (
            "📦 <b>Planes VIP:</b> 7 días S/ 5 | 30 días S/ 20 | "
            "2 meses S/ 35 | Permanente S/ 50\n"
            "✍️ Responde 1, 2, 3 o 4 para seleccionar tu plan.\n\n"
        )
    await event.respond(
        "👑 <b>LOS CHUCKYS — PAGOS VIP</b>\n\n"
        + resumen
        + "🏦 <b>TRANSFERENCIA BANCARIA</b>\n\n"
        + "Número de cuenta:\n"
        + f"<code>{NUMERO_CUENTA}</code>\n\n"
        + "📋 Copia el número en tu aplicación bancaria.\n"
        + "✅ Verifica el banco y el titular antes de transferir.\n\n"
        + "📸 Después de pagar, envíame una <b>foto del comprobante</b> "
        + "para que un asesor revise y confirme tu acceso.",
        parse_mode="html",
    )


async def mandar_recibido(event, plan=None):
    if IMAGEN_ASESOR.is_file():
        try:
            await event.respond(file=str(IMAGEN_ASESOR))
        except FloodWaitError:
            raise
        except Exception as exc:
            logger.warning("Fallo imagen de asesor: %s", exc)
    detalle = ""
    if plan in PLANES:
        datos = PLANES[plan]
        detalle = f"📦 Plan solicitado: {datos['nombre']} — S/ {datos['precio']}\n\n"
    await event.respond(
        "👑 <b>LOS CHUCKYS — VIP</b>\n\n"
        "✅ <b>¡Imagen recibida correctamente!</b>\n\n"
        + detalle
        + "📩 Hemos recibido tu comprobante.\n\n"
        + "⏳ Un asesor se comunicará contigo lo antes posible para "
        + "revisar tu pago y coordinar la activación de tu plan.\n\n"
        + "💜 Gracias por tu confianza.",
        parse_mode="html",
    )


async def mandar_pago(event, uid, plan=None):
    await mandar_qr(event)
    await mandar_cuenta(event, plan)
    # Si falló el envío de los mensajes, no se marca un pago como pendiente.
    fijar_estado(uid, "pendiente", plan)


@client.on(events.NewMessage(incoming=True))
async def responder(event):
    # Por seguridad, no responder a grupos, canales, ni a mensajes salientes.
    if not event.is_private or event.sender_id is None or event.out:
        return
    uid = str(event.sender_id)
    bloqueo = bloqueos.setdefault(uid, asyncio.Lock())

    async with bloqueo:
        try:
            remitente = event.sender
            if remitente is None:
                remitente = await event.get_sender()
            if remitente is not None and getattr(remitente, "bot", False):
                return

            texto = (event.raw_text or "").strip()
            estado = estado_actual(uid)
            codigo = detectar_plan(texto)
            logger.info(
                "Privado de %s | imagen=%s | estado=%s | plan=%s",
                uid, es_imagen(event), estado["estado"] if estado else None, codigo
            )

            # A) Fotografías: acusar recibo del comprobante para este cliente.
            if es_imagen(event):
                if estado and estado["estado"] == "pendiente":
                    await mandar_recibido(event, estado.get("plan"))
                    fijar_estado(uid, "revision", estado.get("plan"))
                elif estado and estado["estado"] == "revision":
                    await event.respond(
                        "✅ Recibí tu nueva imagen. El comprobante sigue pendiente "
                        "de verificación por un asesor."
                    )
                else:
                    await event.respond(
                        "📸 Recibí tu imagen. Si deseas comprar VIP, escribe /vip "
                        "para ver los planes y precios."
                    )
                return

            # B) Solicitan un plan concreto: incluso si estaban en revisión.
            # Ejemplo: Hola, quiero comprar el plan VIP de 7 DÍAS por S/ 5.
            if codigo is not None:
                await mandar_pago(event, uid, codigo)
                return

            # C) Comando /vip o consulta general de precios: mostrar los 4 planes.
            # La selección 1-4 vuelve a entrar por el caso B.
            if pide_menu(texto):
                await mandar_menu(event)
                return

            # Reenviar el QR y los datos del plan anterior por petición.
            if normalizar(texto) in ("qr", "/qr", "reenviar", "pagar"):
                plan_anterior = estado.get("plan") if estado else None
                await mandar_pago(event, uid, plan_anterior)
                return

            # D) Siempre responder al resto de los mensajes.
            if estado and estado["estado"] == "revision":
                await event.respond(
                    "✅ Tu comprobante está pendiente de revisión por un asesor.\n"
                    "Si quieres otro plan o conocer precios, escribe /vip."
                )
                return

            if estado and estado["estado"] == "pendiente":
                nombre = ""
                if estado.get("plan") in PLANES:
                    p = PLANES[estado["plan"]]
                    nombre = f" de {p['nombre']} (S/ {p['precio']})"
                await event.respond(
                    "👑 Ya te envié los datos para el pago" + nombre + ".\n"
                    "📸 Cuando pagues, envíame la foto del comprobante.\n"
                    "📋 Para cambiar de plan o consultar precios, escribe /vip.\n"
                    "🔄 Para recibir el QR otra vez, escribe QR."
                )
                return

            # E) Primer mensaje, incluso "hola", audio, sticker, archivo, etc.
            # Mantiene la conducta anterior de enviar QR y cuenta por separado.
            await mandar_pago(event, uid)

        except FloodWaitError as exc:
            logger.warning("Limite de Telegram: esperar %s segundos", exc.seconds)
            await asyncio.sleep(min(max(int(exc.seconds), 1), 3600))
        except Exception:
            logger.exception("Error al contestar al usuario %s", uid)


async def iniciar():
    if API_ID <= 0 or not API_HASH or API_HASH == "PON_AQUI_TU_API_HASH":
        raise RuntimeError("Configura API_ID y API_HASH en bot_vip.py")

    estados.update(cargar_estado())
    await asyncio.to_thread(preparar_qr)

    if "--login" in sys.argv:
        await client.start()
        yo = await client.get_me()
        logger.info("Sesion guardada para: %s", yo.first_name)
        await client.disconnect()
        return

    while True:
        try:
            await client.connect()
            if not await client.is_user_authorized():
                logger.error("Falta iniciar sesion: python3 bot_vip.py --login")
                return
            yo = await client.get_me()
            logger.info(
                "LOS CHUCKYS VIP ACTIVO | %s | menu /vip y pagos habilitados",
                yo.first_name
            )
            await client.run_until_disconnected()
            logger.warning("Telegram desconectado; se intentara reconectar")
        except asyncio.CancelledError:
            raise
        except KeyboardInterrupt:
            raise
        except Exception:
            logger.exception("Conexion de Telegram interrumpida")
        finally:
            try:
                await client.disconnect()
            except Exception:
                logger.exception("Error al cerrar conexion anterior")
        logger.info("Reconexion en %s segundos", RECONEXION_SEGUNDOS)
        await asyncio.sleep(RECONEXION_SEGUNDOS)


if __name__ == "__main__":
    try:
        asyncio.run(iniciar())
    except KeyboardInterrupt:
        logger.info("Detenido manualmente")
    except Exception:
        logger.exception("No se pudo iniciar el programa")
        sys.exit(1)
