import asyncio
import json
import logging
import os
import sys
import time
import urllib.request
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

# ================== CONFIGURACION ==================
API_ID = 34501633
# Puedes escribir tu API hash entre las comillas o usar TELEGRAM_API_HASH.
API_HASH = os.getenv("TELEGRAM_API_HASH", "PON_AQUI_TU_API_HASH")

BASE = Path(__file__).resolve().parent
SESION = BASE / "loschuckys_sesion"  # Misma sesion del proyecto anterior
QR_URL = "https://i.postimg.cc/sfccGwZ0/image.png"
QR_ARCHIVO = BASE / "qr_pago.png"
IMAGEN_ASESOR = BASE / "asesor.png"  # Opcional: imagen enviada antes del texto
NUMERO_CUENTA = "09400141002543052450"
ESTADO_ARCHIVO = BASE / "estado_vip.json"
VIGENCIA = 7 * 24 * 60 * 60
RECONEXION_SEGUNDOS = 10

# Evita guardar una cantidad ilimitada de archivos de registro.
logger = logging.getLogger("vip")
logger.setLevel(logging.INFO)
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

# Estados independientes para cada cliente.
estados = {}
bloqueos = {}


def cargar_estado():
    if not ESTADO_ARCHIVO.exists():
        return {}
    try:
        datos = json.loads(ESTADO_ARCHIVO.read_text(encoding="utf-8"))
        resultado = {}
        ahora = time.time()
        for id_usuario, valor in datos.items():
            # Compatible con el archivo anterior: {"123": 1760000000.0}
            if isinstance(valor, (int, float)):
                valor = {"fecha": float(valor), "estado": "pendiente"}
            if not isinstance(valor, dict):
                continue
            fecha = float(valor.get("fecha", 0))
            if 0 <= ahora - fecha < VIGENCIA:
                estado = valor.get("estado", "pendiente")
                resultado[str(id_usuario)] = {
                    "fecha": fecha,
                    "estado": estado if estado in ("pendiente", "revision") else "pendiente",
                }
        return resultado
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("No se pudo leer el estado anterior: %s", exc)
        return {}


def guardar_estado():
    try:
        temporal = ESTADO_ARCHIVO.with_suffix(".tmp")
        temporal.write_text(json.dumps(estados, indent=2), encoding="utf-8")
        temporal.replace(ESTADO_ARCHIVO)
    except OSError:
        logger.exception("No se pudo guardar estado_vip.json")


def estado_actual(uid):
    dato = estados.get(uid)
    if dato and time.time() - dato["fecha"] >= VIGENCIA:
        estados.pop(uid, None)
        guardar_estado()
        return None
    return dato["estado"] if dato else None


def fijar_estado(uid, nuevo):
    estados[uid] = {"fecha": time.time(), "estado": nuevo}
    guardar_estado()


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
        # No detenemos el programa por un QR que no se pueda descargar.
        logger.warning("No se pudo descargar el QR: %s", exc)


def es_imagen(event):
    if event.message.photo:
        return True
    archivo = event.message.file
    return bool(archivo and (archivo.mime_type or "").startswith("image/"))


async def mandar_qr(event):
    # event.respond resuelve el chat de este mensaje privado; no buscar usuario por ID.
    if QR_ARCHIVO.is_file() and QR_ARCHIVO.stat().st_size > 0:
        try:
            await event.respond(file=str(QR_ARCHIVO))
            logger.info("QR enviado a %s", event.sender_id)
            return
        except Exception as exc:
            logger.warning("No se pudo adjuntar QR para %s: %s", event.sender_id, exc)
    await event.respond("📲 QR de pago:\n" + QR_URL)


async def mandar_cuenta(event):
    # Segundo mensaje, separado de la imagen QR.
    await event.respond(
        "🏦 <b>TRANSFERENCIA BANCARIA</b>\n\n"
        "Número de cuenta:\n"
        f"<code>{NUMERO_CUENTA}</code>\n\n"
        "📋 Copia el número en tu aplicación bancaria.\n"
        "✅ Verifica el banco y el titular antes de transferir.\n\n"
        "📸 Cuando realices el pago, envía una foto del comprobante a este chat.",
        parse_mode="html",
    )


async def mandar_recibido(event):
    if IMAGEN_ASESOR.is_file():
        try:
            await event.respond(file=str(IMAGEN_ASESOR))
        except Exception as exc:
            logger.warning("No se pudo adjuntar imagen asesor: %s", exc)
    await event.respond(
        "👑 <b>LOS CHUCKYS — VIP</b>\n\n"
        "✅ <b>¡Imagen recibida correctamente!</b>\n\n"
        "📩 Hemos recibido tu comprobante.\n\n"
        "⏳ Un asesor se comunicará contigo lo antes posible para revisar "
        "tu pago y coordinar la entrega de tu producto.\n\n"
        "💜 Gracias por tu confianza.",
        parse_mode="html",
    )


@client.on(events.NewMessage(incoming=True))
async def responder(event):
    # Solo mensajes privados entrantes, nunca grupos ni canales.
    if not event.is_private or event.sender_id is None:
        return
    uid = str(event.sender_id)
    bloqueo = bloqueos.setdefault(uid, asyncio.Lock())

    async with bloqueo:
        try:
            # Evita conversaciones automaticas entre bots.
            remitente = event.sender
            if remitente is not None and getattr(remitente, "bot", False):
                return

            texto = (event.raw_text or "").strip().lower()
            estado = estado_actual(uid)
            logger.info("Mensaje privado de %s | imagen=%s | estado=%s", uid, es_imagen(event), estado)

            if es_imagen(event):
                if estado == "pendiente":
                    # Marcar revision solo despues de conseguir responder.
                    await mandar_recibido(event)
                    fijar_estado(uid, "revision")
                elif estado == "revision":
                    await event.respond(
                        "✅ Recibí tu nueva imagen. Tu comprobante sigue en revisión; "
                        "un asesor se comunicará contigo."
                    )
                else:
                    await event.respond(
                        "📸 Imagen recibida. Si es un comprobante, un asesor lo revisará. "
                        "Para recibir QR y cuenta escribe: VIP"
                    )
                return

            # VIP/QR/REENVIAR siempre permite solicitar datos otra vez.
            if texto in ("vip", "/vip", "qr", "reenviar", "/start", "pagar"):
                fijar_estado(uid, "pendiente")
                await mandar_qr(event)
                await mandar_cuenta(event)
                return

            if estado == "revision":
                await event.respond(
                    "👑 LOS CHUCKYS — VIP\n\n"
                    "✅ Ya recibimos tu comprobante.\n"
                    "⏳ Un asesor revisará el pago y se comunicará contigo.\n"
                    "📲 Para recibir los datos de pago otra vez escribe: VIP"
                )
                return

            if estado == "pendiente":
                await event.respond(
                    "👑 LOS CHUCKYS — VIP\n\n"
                    "✅ Ya te envié el QR y el número de cuenta.\n"
                    "📸 Envíame una imagen del comprobante cuando realices el pago.\n"
                    "🔄 Si necesitas recibirlos otra vez escribe: VIP"
                )
                return

            # PRIMER mensaje de una persona: cualquier texto, audio, sticker, etc.
            # No exige que escriba "VIP", "comprar" ni "precio".
            fijar_estado(uid, "pendiente")
            await mandar_qr(event)
            await mandar_cuenta(event)

        except FloodWaitError as exc:
            logger.warning("Telegram aplico espera de %s segundos", exc.seconds)
            # No se cierra el bot. Se respeta el limite del servidor.
            await asyncio.sleep(min(int(exc.seconds), 3600))
        except Exception:
            # Un error en un chat jamas debe cerrar los demas chats.
            logger.exception("Error respondiendo al usuario %s", uid)


async def iniciar():
    if API_ID <= 0 or not API_HASH or API_HASH == "PON_AQUI_TU_API_HASH":
        raise RuntimeError("Configura API_ID y API_HASH en bot_vip.py")

    estados.update(cargar_estado())
    await asyncio.to_thread(preparar_qr)

    if "--login" in sys.argv:
        # La primera vez es interactiva: Telegram pide telefono/codigo.
        await client.start()
        yo = await client.get_me()
        logger.info("Inicio de sesion guardado para: %s", yo.first_name)
        await client.disconnect()
        return

    while True:
        try:
            await client.connect()
            if not await client.is_user_authorized():
                logger.error("Falta iniciar sesion. Ejecuta: python3 bot_vip.py --login")
                return
            yo = await client.get_me()
            logger.info("LOS CHUCKYS VIP ACTIVO | %s | esperando mensajes privados", yo.first_name)
            await client.run_until_disconnected()
            logger.warning("Telegram se desconecto. Se intentara reconectar.")
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
                logger.exception("Error al cerrar la conexion anterior")
        logger.info("Nuevo intento en %s segundos", RECONEXION_SEGUNDOS)
        await asyncio.sleep(RECONEXION_SEGUNDOS)


if __name__ == "__main__":
    try:
        asyncio.run(iniciar())
    except KeyboardInterrupt:
        logger.info("Detenido manualmente")
    except Exception:
        logger.exception("No se pudo iniciar el programa")
        sys.exit(1)
