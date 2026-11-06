# INSTALAR: py -m pip install -U telethon
# EJECUTAR: py loschuckys_vip_corregido.py

import asyncio
import json
import re
import time
import unicodedata
import urllib.request
from pathlib import Path

from telethon import TelegramClient, events

# =====================================
# 1. CONFIGURACION
# =====================================
API_ID = 34501633
API_HASH = "cc60b8ad4b4e95ab0908e6ef379efc49"

BASE = Path(__file__).resolve().parent
QR_URL = "https://i.postimg.cc/sfccGwZ0/image.png"
QR_ARCHIVO = BASE / "qr_pago.png"
IMAGEN_ASESOR = BASE / "asesor.png"  # Opcional
NUMERO_CUENTA = "09400141002543052450"
ESTADO_ARCHIVO = BASE / "estado_vip.json"
VIGENCIA = 7 * 24 * 60 * 60  # 7 dias

client = TelegramClient(str(BASE / "loschuckys_sesion"), API_ID, API_HASH)

# Cada usuario tiene su estado y bloqueo independiente.
pendientes = {}
bloqueos = {}


# =====================================
# 2. GUARDAR Y LEER ESTADO
# =====================================
def cargar_estado():
    if not ESTADO_ARCHIVO.exists():
        return {}
    try:
        datos = json.loads(ESTADO_ARCHIVO.read_text(encoding="utf-8"))
        ahora = time.time()
        # Compatible con el estado anterior: {"usuario_id": fecha}.
        return {
            str(uid): float(fecha)
            for uid, fecha in datos.items()
            if isinstance(fecha, (int, float))
            and 0 <= ahora - float(fecha) < VIGENCIA
        }
    except (OSError, ValueError, TypeError, AttributeError):
        print("Aviso: no se pudo leer estado_vip.json; se inicia vacio")
        return {}


def guardar_estado():
    try:
        temporal = ESTADO_ARCHIVO.with_suffix(".tmp")
        temporal.write_text(
            json.dumps(pendientes, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporal.replace(ESTADO_ARCHIVO)
    except OSError as error:
        print("Error al guardar estado:", error)


def esta_pendiente(uid):
    fecha = pendientes.get(str(uid))
    if fecha is None:
        return False
    if time.time() - fecha >= VIGENCIA:
        pendientes.pop(str(uid), None)
        guardar_estado()
        return False
    return True


# =====================================
# 3. QR Y DETECCION
# =====================================
def preparar_qr():
    if QR_ARCHIVO.exists() and QR_ARCHIVO.stat().st_size > 0:
        print("QR local encontrado")
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
        print("QR descargado")
    except Exception as error:
        print("No se pudo descargar el QR:", error)
        print("Se enviara un enlace al QR como alternativa")


def normalizar(texto):
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(
        letra for letra in texto if unicodedata.category(letra) != "Mn"
    ).strip()


def es_consulta_vip(texto):
    # No exige dos palabras a la vez. Detecta consultas comunes.
    texto = normalizar(texto)
    return bool(re.search(
        r"\b(vip|planes?|comprar|compra|precios?|cuanto|"
        r"pagar|pago|transferencia|activar|activacion|"
        r"activame|renovar|membresia|grupo\s+vip|"
        r"informacion\s+vip|licencia)\b",
        texto,
    ))


def es_imagen(event):
    if event.message.photo:
        return True
    archivo = event.message.file
    return bool(archivo and (archivo.mime_type or "").startswith("image/"))


# =====================================
# 4. RESPUESTAS (EN MENSAJES SEPARADOS)
# =====================================
async def enviar_qr(event):
    if QR_ARCHIVO.exists() and QR_ARCHIVO.stat().st_size > 0:
        try:
            destino = await event.get_input_chat()
            if destino is None:
                destino = await event.get_sender()
            if destino is None:
                raise ValueError("Telegram no resolvio este chat")
            await client.send_file(destino, str(QR_ARCHIVO))
            print(f"[{event.sender_id}] QR enviado")
            return
        except Exception as error:
            print(f"[{event.sender_id}] No se pudo adjuntar el QR:", error)

    await event.respond("📲 QR para el pago:\n" + QR_URL)
    print(f"[{event.sender_id}] Enlace de QR enviado")


async def enviar_cuenta(event):
    texto = (
        "🏦 <b>TRANSFERENCIA BANCARIA</b>\n\n"
        "Número de cuenta:\n"
        f"<code>{NUMERO_CUENTA}</code>\n\n"
        "📋 Copia el número en tu aplicación bancaria.\n"
        "✅ Verifica el banco y el titular antes de transferir.\n\n"
        "📸 Cuando realices el pago, envía una imagen del comprobante "
        "a este mismo chat."
    )
    await event.respond(texto, parse_mode="html")
    print(f"[{event.sender_id}] Cuenta enviada")


async def recibir_comprobante(event):
    if IMAGEN_ASESOR.exists():
        try:
            destino = await event.get_input_chat()
            if destino is None:
                destino = await event.get_sender()
            if destino is not None:
                await client.send_file(destino, str(IMAGEN_ASESOR))
        except Exception as error:
            # La respuesta escrita siempre se intenta enviar.
            print(f"[{event.sender_id}] Error en imagen del asesor:", error)

    await event.respond(
        "👑 <b>LOS CHUCKYS — VIP</b>\n\n"
        "✅ <b>¡Imagen recibida!</b>\n\n"
        "📩 Recibimos tu comprobante.\n"
        "⏳ Un asesor revisará el pago y se comunicará contigo "
        "para coordinar la activación del producto.\n\n"
        "💜 Gracias por tu confianza.",
        parse_mode="html",
    )
    print(f"[{event.sender_id}] Comprobante respondido")


# =====================================
# 5. MENSAJES ENTRANTES
# =====================================
@client.on(events.NewMessage(incoming=True))
async def respuesta_automatica(event):
    if not event.is_private or event.sender_id is None:
        return

    uid = str(event.sender_id)
    if uid not in bloqueos:
        bloqueos[uid] = asyncio.Lock()

    # Solo se bloquea la conversacion de ESTE usuario.
    # Otros clientes pueden escribir mientras tanto.
    async with bloqueos[uid]:
        try:
            mensaje = (event.raw_text or "").strip()
            print(f"[{uid}] Mensaje nuevo: {mensaje[:65]!r} | foto={es_imagen(event)}")

            # 1. Si llego una imagen, comprobar si esperaba un pago.
            if es_imagen(event):
                if esta_pendiente(uid):
                    await recibir_comprobante(event)
                    pendientes.pop(uid, None)
                    guardar_estado()
                else:
                    await event.respond(
                        "📸 Recibí tu imagen. Si quieres enviar un "
                        "comprobante VIP, escribe primero <b>VIP</b> "
                        "para recibir los datos de pago.",
                        parse_mode="html",
                    )
                return

            if not mensaje:
                return

            texto = normalizar(mensaje)

            # 2. Solicitudes de compra, planes o activacion.
            if es_consulta_vip(mensaje):
                if esta_pendiente(uid) and texto not in ("vip", "/vip", "reenviar"):
                    await event.respond(
                        "✅ Ya te envié el QR y la cuenta.\n"
                        "📸 Envía tu comprobante aquí.\n"
                        "🔄 Para recibirlos otra vez escribe: VIP"
                    )
                    return

                # Registrar antes de enviar: si falla un envio, la foto
                # posterior del mismo usuario no se pierde.
                pendientes[uid] = time.time()
                guardar_estado()

                # Mensaje 1: imagen QR (sin descripcion).
                await enviar_qr(event)

                # Mensaje 2: datos bancarios (texto independiente).
                await enviar_cuenta(event)
                return

            # 3. Nunca se queda en silencio al recibir un texto privado.
            if esta_pendiente(uid):
                await event.respond(
                    "👑 Tu consulta VIP sigue pendiente.\n"
                    "📸 Envíame la imagen de tu comprobante.\n"
                    "🔄 Escribe VIP para volver a recibir el QR y la cuenta."
                )
            else:
                await event.respond(
                    "👋 ¡Hola! Soy el asistente de LOS CHUCKYS.\n\n"
                    "👑 Para consultar los planes o realizar una compra, "
                    "escribe: VIP"
                )

        except Exception as error:
            print(f"[{uid}] ERROR al responder: {type(error).__name__}: {error}")


# =====================================
# 6. INICIAR
# =====================================
async def iniciar():
    if API_ID <= 0 or API_HASH == "PEGA_AQUI_TU_API_HASH":
        print("ERROR: coloca tu API_ID y API_HASH en el archivo")
        return

    pendientes.update(cargar_estado())
    await asyncio.to_thread(preparar_qr)

    await client.start()
    yo = await client.get_me()
    print("=" * 48)
    print("👑 LOS CHUCKYS — VIP")
    print("Conectado como:", yo.first_name)
    print("✅ Mensajes privados activos")
    print("✅ QR y cuenta se envian por separado")
    print("✅ Cada comprador tiene su propio estado")
    print("✅ Imagenes y comprobantes detectados")
    print("Esperando mensajes...")
    print("=" * 48)

    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        with client:
            client.loop.run_until_complete(iniciar())
    except KeyboardInterrupt:
        print("Programa detenido")
    except Exception as error:
        print("Error al iniciar:", type(error).__name__, error)
