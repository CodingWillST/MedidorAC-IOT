import flet as ft
import socket
import struct
import json
import random
import string
import threading
import time

# Configuración del broker MQTT
BROKER = "broker.emqx.io"
PORT = 1883

# Topics MQTT
TOPIC_VOLTAJE = "medicion/voltaje"
TOPIC_CORRIENTE = "medicion/corriente"
TOPIC_FRECUENCIA = "medicion/factor_potencia"
TOPIC_POTENCIA = "medicion/potencia"
TOPIC_ESTADO = "medicion/estado"
TOPIC_CONTROL_RESET = "control/reset"

MAX_DATA_POINTS = 50  # Número máximo de puntos en las gráficas

class MQTTClient:
    def __init__(self, on_message_callback):
        self.client_id = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(8))
        self.on_message_callback = on_message_callback
        self.connected = False
        self.socket = None
        self.thread = None
        self.running = False
        self.keepalive = 30
        self.packet_id = 0
        self.last_ping = 0
        self.subscribed_topics = set()
        self.reconnect_thread = None
        self.reconnect_delay = 5  # Segundos entre intentos de reconexión
        self.max_reconnect_attempts = 5
        
    def _get_packet_id(self):
        self.packet_id = (self.packet_id + 1) % 65536
        return self.packet_id
        
    def _pack_string(self, string):
        return struct.pack("!H", len(string)) + string.encode()
        
    def _read_remaining_length(self):
        multiplier = 1
        value = 0
        while True:
            byte = self.socket.recv(1)[0]
            value += (byte & 0x7F) * multiplier
            multiplier *= 128
            if not (byte & 0x80):
                break
        return value
        
    def _send_packet(self, packet_type, payload=b""):
        try:
            remaining_length = len(payload)
            header = bytearray([packet_type])
            
            # Codificar la longitud restante
            while remaining_length > 0:
                byte = remaining_length % 128
                remaining_length = remaining_length // 128
                if remaining_length > 0:
                    byte = byte | 0x80
                header.append(byte)
            
            # Enviar el paquete completo
            self.socket.send(bytes(header) + payload)
            return True
        except Exception as e:
            print(f"Error al enviar paquete: {str(e)}")
            self.connected = False
            return False

    def _maintain_connection(self):
        while self.running:
            try:
                if not self.connected:
                    print("Intentando reconectar al broker MQTT...")
                    if self.connect():
                        print("Reconexión exitosa")
                        # Resuscribirse a los topics
                        for topic in self.subscribed_topics:
                            self._subscribe(topic)
                    else:
                        print(f"Error en la reconexión, reintentando en {self.reconnect_delay} segundos...")
                time.sleep(self.reconnect_delay)
            except Exception as e:
                print(f"Error en el mantenimiento de conexión: {str(e)}")
                time.sleep(self.reconnect_delay)
        
    def connect(self):
        try:
            # Crear y configurar el socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(5)
            self.socket.connect((BROKER, PORT))
            
            # Construir paquete CONNECT
            protocol_name = "MQTT"
            protocol_version = 4
            connect_flags = 0x02  # Clean session
            
            # Construir el payload
            payload = bytearray()
            
            # Protocol name
            payload.extend(struct.pack("!H", len(protocol_name)))
            payload.extend(protocol_name.encode())
            
            # Protocol version
            payload.append(protocol_version)
            
            # Connect flags
            payload.append(connect_flags)
            
            # Keep alive
            payload.extend(struct.pack("!H", self.keepalive))
            
            # Client ID
            payload.extend(struct.pack("!H", len(self.client_id)))
            payload.extend(self.client_id.encode())
            
            # Enviar paquete CONNECT
            if not self._send_packet(0x10, payload):
                return False
            
            # Recibir CONNACK
            try:
                header = self.socket.recv(1)
                if not header:
                    print("No se recibió respuesta del servidor")
                    return False
                
                packet_type = header[0] >> 4
                if packet_type != 0x02:  # CONNACK
                    print(f"Paquete inesperado: {packet_type}")
                    return False
                
                remaining_length = self._read_remaining_length()
                if remaining_length != 2:
                    print(f"Longitud de CONNACK inválida: {remaining_length}")
                    return False
                
                response = self.socket.recv(2)
                if response[1] != 0:
                    print(f"Código de retorno CONNACK: {response[1]}")
                    return False
                
                self.connected = True
                self.running = True
                self.last_ping = time.time()
                
                # Iniciar hilo de recepción
                self.thread = threading.Thread(target=self._receive_loop)
                self.thread.daemon = True
                self.thread.start()
                
                # Iniciar hilo de mantenimiento de conexión
                if self.reconnect_thread is None or not self.reconnect_thread.is_alive():
                    self.reconnect_thread = threading.Thread(target=self._maintain_connection)
                    self.reconnect_thread.daemon = True
                    self.reconnect_thread.start()
                
                # Suscribirse a los topics solo si no están suscritos
                topics = [TOPIC_VOLTAJE, TOPIC_CORRIENTE, TOPIC_FRECUENCIA, TOPIC_POTENCIA, TOPIC_ESTADO]
                for topic in topics:
                    if topic not in self.subscribed_topics:
                        self._subscribe(topic)
                        self.subscribed_topics.add(topic)
                
                return True
                
            except socket.timeout:
                print("Timeout al esperar CONNACK")
                return False
                
        except Exception as e:
            print(f"Error al conectar: {str(e)}")
            self.connected = False
            return False
    
    def disconnect(self):
        self.running = False
        if self.connected:
            try:
                self._send_packet(0xE0)  # DISCONNECT
            except:
                pass
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        self.connected = False
        self.subscribed_topics.clear()
    
    def _subscribe(self, topic):
        if self.connected:
            try:
                packet_id = self._get_packet_id()
                
                # Construir payload
                payload = bytearray()
                payload.extend(struct.pack("!H", packet_id))
                payload.extend(struct.pack("!H", len(topic)))
                payload.extend(topic.encode())
                payload.append(0)  # QoS
                
                # Enviar paquete SUBSCRIBE
                if not self._send_packet(0x82, payload):
                    print(f"Error al enviar SUBSCRIBE para {topic}")
                    return
                
                # Esperar SUBACK con timeout más corto
                try:
                    self.socket.settimeout(2)  # Timeout de 2 segundos para SUBACK
                    header = self.socket.recv(1)
                    if not header:
                        return
                    
                    packet_type = header[0] >> 4
                    if packet_type != 0x09:  # SUBACK
                        return
                    
                    remaining_length = self._read_remaining_length()
                    if remaining_length != 3:
                        return
                    
                    response = self.socket.recv(3)
                    if response[0] != packet_id >> 8 or response[1] != packet_id & 0xFF:
                        return
                    
                    self.socket.settimeout(5)  # Restaurar timeout normal
                    
                except socket.timeout:
                    print(f"Timeout al esperar SUBACK para {topic}")
                    self.socket.settimeout(5)  # Restaurar timeout normal
                
            except Exception as e:
                print(f"Error al suscribirse a {topic}: {str(e)}")
    
    def publish(self, topic, message):
        if self.connected:
            try:
                # Construir payload
                payload = bytearray()
                payload.extend(struct.pack("!H", len(topic)))
                payload.extend(topic.encode())
                payload.extend(message.encode())
                
                # Enviar paquete PUBLISH
                return self._send_packet(0x30, payload)
                
            except Exception as e:
                print(f"Error al publicar en {topic}: {str(e)}")
                return False
        return False
    
    def _receive_loop(self):
        while self.running:
            try:
                # Verificar keepalive
                current_time = time.time()
                if current_time - self.last_ping > self.keepalive:
                    if not self._send_packet(0xC0):  # PINGREQ
                        break
                    self.last_ping = current_time
                
                # Leer el header del paquete
                header = self.socket.recv(1)
                if not header:
                    break
                
                packet_type = header[0] >> 4
                remaining_length = self._read_remaining_length()
                
                # Leer el resto del paquete
                packet = self.socket.recv(remaining_length)
                
                if packet_type == 0x03:  # PUBLISH
                    # Extraer topic y payload
                    topic_length = struct.unpack("!H", packet[:2])[0]
                    topic = packet[2:2+topic_length].decode()
                    payload = packet[2+topic_length:].decode()
                    
                    if self.on_message_callback:
                        self.on_message_callback(topic, payload)
                        
                elif packet_type == 0x0D:  # PINGRESP
                    self.last_ping = current_time
                    
            except socket.timeout:
                continue
            except Exception as e:
                print(f"Error en loop de recepción: {str(e)}")
                break
        
        self.connected = False

def get_status_and_color(value, ranges, variable_type):
    if variable_type == "voltage":
        if value < ranges[0]:
            return "Bajo", ft.Colors.BLUE
        elif value > ranges[1]:
            return "Alto", ft.Colors.RED
        return "Normal", ft.Colors.GREEN
    elif variable_type == "current":
        if value > ranges[1]:
            return "Alto", ft.Colors.RED
        return "Normal", ft.Colors.GREEN
    elif variable_type == "power":
        if value > ranges[1]:
            return "Alto", ft.Colors.RED
        return "Normal", ft.Colors.GREEN
    else:  # frequency
        if value < ranges[0]:
            return "Bajo", ft.Colors.BLUE
        elif value > ranges[1]:
            return "Alto", ft.Colors.RED
        return "Normal", ft.Colors.GREEN

def crear_tarjeta_medicion(titulo, valor, unidad, icono, color_fondo, color_icono, ranges, variable_type, page, decimals=1, scale=1.0):
    # Crear elementos de la tarjeta
    progress = ft.ProgressRing(
        width=60,
        height=60,
        stroke_width=6,
        color=color_fondo,
        value=0,
        visible=False
    )
    
    value_text = ft.Text(
        value="--", 
        size=20,
        weight=ft.FontWeight.BOLD, 
        color=color_fondo
    )
    
    unidad_text = ft.Text(
        unidad,
        size=10,
        color=color_fondo
    )
    
    status_indicator = ft.Container(
        width=6, 
        height=6, 
        border_radius=3, 
        bgcolor=ft.Colors.GREY_400
    )
    
    status_text = ft.Text(
        "Esperando datos...", 
        color=ft.Colors.GREY_400, 
        size=9
    )

    # Contenedor para el valor y unidad
    value_container = ft.Container(
        content=ft.Column(
            controls=[
                value_text,
                unidad_text
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=0,
            alignment=ft.MainAxisAlignment.CENTER
        ),
        alignment=ft.alignment.center,
        width=60,
        height=60,
        margin=ft.margin.only(top=2)
    )

    # Contenedor principal
    card = ft.Card(
        content=ft.Container(
            width=120,
            height=120,
            padding=8,
            bgcolor=ft.Colors.with_opacity(0.1, color_fondo),
            border_radius=8,
            alignment=ft.alignment.center,
            content=ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=4,
                controls=[
                    ft.Icon(name=icono, size=20, color=color_fondo),
                    ft.Text(titulo, size=12, weight=ft.FontWeight.BOLD, color=color_fondo),
                    ft.Container(
                        content=ft.Stack(
                            [
                                progress,
                                value_container
                            ],
                            alignment=ft.alignment.center
                        ),
                        alignment=ft.alignment.center,
                        width=60,
                        height=60
                    ),
                    ft.Container(
                        content=ft.Row(
                            [status_indicator, status_text],
                            alignment=ft.MainAxisAlignment.CENTER,
                            spacing=2
                        ),
                        alignment=ft.alignment.center
                    )
                ]
            )
        )
    )

    def update_value(value):
        try:
            value_float = float(value)
            print(f"Actualizando {titulo}: {value_float}")
            
            # Si es el primer valor, mostrar el progress ring
            if not progress.visible:
                progress.visible = True
            
            # Escalar/convertir valor para mostrar (ej. W -> kW)
            displayed = value_float / scale if scale != 1.0 else value_float

            # Actualizar el valor con la cantidad de decimales requerida
            value_text.value = f"{displayed:.{decimals}f}"

            # Calcular el progreso basado en el rango (usar el valor mostrado)
            progress_val = (displayed - ranges[0]) / (ranges[1] - ranges[0])
            progress.value = max(0, min(1, progress_val))
            
            # Actualizar estado y color
            status, color = get_status_and_color(value_float, ranges, variable_type)
            status_text.value = f"Estado: {status}"
            status_text.color = color
            status_indicator.bgcolor = color
            
            # Forzar actualización
            page.update()
            
        except ValueError as e:
            print(f"Error al actualizar {titulo}: {str(e)}")
            # En caso de error, volver al estado inicial
            progress.visible = False
            value_text.value = "--"
            status_text.value = "Error en datos"
            status_text.color = ft.Colors.RED
            status_indicator.bgcolor = ft.Colors.RED
            page.update()

    return card, update_value

def main(page: ft.Page):
    page.title = "Sistema de Monitoreo y Control Eléctrico IoT"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 5
    page.window_width = 400
    page.window_height = 800
    
    # Valores por defecto y configuración
    min_voltage = 80.0  # Ajustado para rango más amplio
    max_voltage = 220.0  # Ajustado para rango más amplio
    max_current = 40.0   # Ajustado a 40A
    mqtt_connected = False
    
    # Sistema de estado para alertas
    alertas_activas = {
        "bajo_voltaje": False,
        "sobrevoltaje": False,
        "sobrecorriente": False
    }
    
    voltaje_data_points = []
    corriente_data_points = []
    
    # Elementos de la interfaz - Monitoreo
    voltaje_value = ft.Text("--", size=40, weight=ft.FontWeight.BOLD)
    corriente_value = ft.Text("--", size=40, weight=ft.FontWeight.BOLD)
    potencia_value = ft.Text("--", size=40, weight=ft.FontWeight.BOLD)
    frecuencia_value = ft.Text("--", size=40, weight=ft.FontWeight.BOLD)
    estado_value = ft.Text("Desconectado", size=20, color=ft.Colors.GREY)
    connection_status = ft.Text("Desconectado del broker MQTT", color=ft.Colors.RED)
    
    # Elementos de la interfaz - Control
    min_voltage_value = ft.Text(f"{min_voltage:.1f} V")
    max_voltage_value = ft.Text(f"{max_voltage:.1f} V")
    max_current_value = ft.Text(f"{max_current:.1f} A")
    switch_carga = ft.Switch(label="", value=False)
    estado_carga = ft.Text("Desactivada", color=ft.Colors.RED)
    status_text = ft.Text("", italic=True, size=14)
    
    # Crear instancia de controller MQTT
    def on_mqtt_message(topic, valor):
        # Esta función se ejecuta cuando llega un mensaje MQTT
        try:
            # Limpiar y validar el valor
            valor = valor.strip()
            if not valor:  # Si el valor está vacío, ignorar
                return
            
            if topic == TOPIC_ESTADO:
                # Manejar el estado como string
                if valor == "ACTIVO":
                    estado_carga.value = "Activada ✅"
                    estado_carga.color = ft.Colors.GREEN
                else:
                    estado_carga.value = "Desactivada ❌"
                    estado_carga.color = ft.Colors.RED
                page.update()
                return
            
            # Para los demás topics, convertir a float
            valor_float = float(valor)
            print(f"Procesando mensaje MQTT - Topic: {topic}, Valor: {valor_float}")
            
            if topic == TOPIC_VOLTAJE:
                # Actualizar valor y dashboard
                voltaje_value.value = f"{valor_float:.1f}"
                update_voltaje(valor_float)
                
                # Actualizar estado de alertas
                alertas_activas["bajo_voltaje"] = valor_float < min_voltage
                alertas_activas["sobrevoltaje"] = valor_float > max_voltage
                
                # Actualizar datos de gráficas solo si el valor es válido
                if 0 <= valor_float <= 300:  # Rango razonable para voltaje
                    voltaje_data_points.append(ft.LineChartDataPoint(len(voltaje_data_points), valor_float))
                    if len(voltaje_data_points) > MAX_DATA_POINTS:
                        voltaje_data_points.pop(0)
                        # Actualizar coordenadas X
                        for i, point in enumerate(voltaje_data_points):
                            point.x = i
                    voltaje_chart.data_series[0].data_points = voltaje_data_points
                    voltaje_chart.update()
                
            elif topic == TOPIC_CORRIENTE:
                # Actualizar valor y dashboard
                corriente_value.value = f"{valor_float:.1f}"
                update_corriente(valor_float)
                
                # Actualizar estado de alertas
                alertas_activas["sobrecorriente"] = valor_float > max_current
                
                # Actualizar datos de gráficas solo si el valor es válido
                if 0 <= valor_float <= 50:  # Rango razonable para corriente
                    corriente_data_points.append(ft.LineChartDataPoint(len(corriente_data_points), valor_float))
                    if len(corriente_data_points) > MAX_DATA_POINTS:
                        corriente_data_points.pop(0)
                        # Actualizar coordenadas X
                        for i, point in enumerate(corriente_data_points):
                            point.x = i
                    corriente_chart.data_series[0].data_points = corriente_data_points
                    corriente_chart.update()
                
            elif topic == TOPIC_FRECUENCIA:
                # Este topic contiene el factor de potencia (0..1). Mostrar con 2 decimales
                if 0.0 <= valor_float <= 2.0:  # aceptar hasta 2 por robustez
                    frecuencia_value.value = f"{valor_float:.2f}"
                    update_frecuencia(valor_float)
                
            elif topic == TOPIC_POTENCIA:
                # Actualizar valor y dashboard solo si es positivo
                if valor_float >= 0:
                    # Detectar si el valor viene en W (por ejemplo >100) o ya en kW
                    if valor_float > 100:  # asumir W -> convertir a kW
                        kw = valor_float / 1000.0
                    else:
                        kw = valor_float  # ya está en kW

                    # Mostrar en la pantalla principal en kW (3 decimales)
                    potencia_value.value = f"{kw:.3f}"
                    # Enviar siempre kW a la tarjeta (la tarjeta usa escala=1.0)
                    update_potencia(kw)

            # Actualizar el estado del sistema
            actualizar_estado_sistema()
            
            # Forzar actualización de la página
            page.update()
            
        except ValueError as e:
            print(f"Error al procesar mensaje MQTT en {topic}: {valor} - {str(e)}")
        except Exception as e:
            print(f"Error inesperado al procesar mensaje MQTT: {str(e)}")
    
    mqtt_controller = MQTTClient(on_mqtt_message)
    
    # Función para conectar al broker MQTT
    def connect_mqtt(e):
        nonlocal mqtt_connected
        
        # Botón para conectar/desconectar
        btn_connect = e.control
        
        if not mqtt_connected:
            btn_connect.text = "Conectando..."
            btn_connect.disabled = True
            page.update()
            
            connection_status.value = "Conectando al broker MQTT..."
            connection_status.color = ft.Colors.ORANGE
            page.update()
            
            # Iniciar conexión
            if mqtt_controller.connect():
                # Actualizar después de unos segundos para dar tiempo a la conexión
                def update_status():
                    # Esperar a que se establezca la conexión
                    wait_time = 0
                    while wait_time < 5 and not mqtt_controller.connected:
                        time.sleep(0.5)
                        wait_time += 0.5
                    
                    if mqtt_controller.connected:
                        connection_status.value = f"Conectado al broker MQTT con ID: {mqtt_controller.client_id}"
                        connection_status.color = ft.Colors.GREEN
                        btn_connect.text = "Desconectar"
                        btn_connect.icon = ft.Icons.LINK_OFF
                        mqtt_connected = True
                    else:
                        connection_status.value = "Error al conectar al broker MQTT"
                        connection_status.color = ft.Colors.RED
                        btn_connect.text = "Conectar"
                        btn_connect.icon = ft.Icons.LINK
                    
                    btn_connect.disabled = False
                    page.update()
                
                # Ejecutar en un hilo separado
                threading.Thread(target=update_status).start()
            else:
                connection_status.value = "Error al conectar al broker MQTT"
                connection_status.color = ft.Colors.RED
                btn_connect.text = "Conectar"
                btn_connect.disabled = False
                page.update()
        else:
            # Desconectar
            btn_connect.text = "Desconectando..."
            btn_connect.disabled = True
            page.update()
            
            mqtt_controller.disconnect()
            mqtt_connected = False
            
            connection_status.value = "Desconectado del broker MQTT"
            connection_status.color = ft.Colors.RED
            
            btn_connect.text = "Conectar"
            btn_connect.icon = ft.Icons.LINK
            btn_connect.disabled = False
            page.update()
    
    # Función para generar datos simulados (modo demo)
    def generate_demo_data(e):
        if not mqtt_connected:
            status_text.value = "Error: No hay conexión MQTT para enviar datos demo"
            page.update()
            return
        
        # Publicar datos simulados con valores ajustados para 220V
        mqtt_controller.publish(TOPIC_VOLTAJE, f"{random.uniform(200.0, 240.0):.1f}")  # Ajustado para 220V
        mqtt_controller.publish(TOPIC_CORRIENTE, f"{random.uniform(0.0, 40.0):.1f}")   # Ajustado a 40A máx
        mqtt_controller.publish(TOPIC_POTENCIA, f"{random.uniform(500, 8800):.1f}")    # Ajustado para 220V
        mqtt_controller.publish(TOPIC_FRECUENCIA, f"{random.uniform(59.5, 60.5):.1f}") # Ajustado para 60Hz
    
    # Función para enviar comando de control
    def send_control_command(e):
        if not mqtt_controller.connected:
            status_text.value = "Error: No hay conexión MQTT para enviar comandos"
            page.update()
            return

        carga_activa = switch_carga.value
        comando = "1" if carga_activa else "0"  # Cambiado a 1/0 para mejor compatibilidad

        # Publicar comando de control
        if mqtt_controller.publish(TOPIC_CONTROL_RESET, comando):
            estado_carga.value = f"{'Activada ✅' if carga_activa else 'Desactivada ❌'}"
            estado_carga.color = ft.Colors.GREEN if carga_activa else ft.Colors.RED
            
            # Publicar ajustes de voltaje y corriente con un pequeño retraso
            def enviar_ajustes():
                time.sleep(0.5)  # Pequeño retraso para asegurar el orden
                mqtt_controller.publish("medicion/VAC_MIN", f"{min_voltage:.1f}")
                time.sleep(0.1)
                mqtt_controller.publish("medicion/VAC_MAX", f"{max_voltage:.1f}")
                time.sleep(0.1)
                mqtt_controller.publish("medicion/AMP_MAX", f"{max_current:.1f}")
            
            # Ejecutar en un hilo separado para no bloquear la UI
            threading.Thread(target=enviar_ajustes).start()
            
            status_text.value = f"Comando enviado correctamente: {comando} ✅"
        else:
            status_text.value = "Error al enviar comando ❌"

        # Actualizar la interfaz
        page.update()
    
    # Funciones para actualizar límites de alarma
    def update_min_voltage(e):
        nonlocal min_voltage
        min_voltage = e.control.value
        min_voltage_value.value = f"{min_voltage:.1f} V"
        
        # Verificar si el voltaje actual está fuera de límites
        if voltaje_value.value != "--":
            try:
                valor_actual = float(voltaje_value.value)
                alertas = []
                if valor_actual < min_voltage:
                    alertas.append("Bajo Voltaje ⚠️")
                elif valor_actual > max_voltage:
                    alertas.append("Sobrevoltaje ⚠️")
                
                # Verificar también la corriente
                if corriente_value.value != "--":
                    try:
                        corriente_actual = float(corriente_value.value)
                        if corriente_actual > max_current:
                            alertas.append("Sobrecorriente ⚠️")
                    except ValueError:
                        pass
                
                if alertas:
                    estado_value.value = " | ".join(alertas)
                    estado_value.color = ft.Colors.RED
                else:
                    estado_value.value = "Óptimo ✅"
                    estado_value.color = ft.Colors.GREEN
            except ValueError:
                print("Error al convertir valor de voltaje actual")
        
        status_text.value = f"Límite de voltaje mínimo actualizado a {min_voltage:.1f} V"
        
        # Publicar nuevo límite al dispositivo
        if mqtt_controller.connected:
            mqtt_controller.publish("medicion/VAC_MIN", f"{min_voltage:.1f}")
        
        page.update()

    def update_max_voltage(e):
        nonlocal max_voltage
        max_voltage = e.control.value
        max_voltage_value.value = f"{max_voltage:.1f} V"
        
        # Verificar si el voltaje actual está fuera de límites
        if voltaje_value.value != "--":
            try:
                valor_actual = float(voltaje_value.value)
                alertas = []
                if valor_actual > max_voltage:
                    alertas.append("Sobrevoltaje ⚠️")
                elif valor_actual < min_voltage:
                    alertas.append("Bajo Voltaje ⚠️")
                
                # Verificar también la corriente
                if corriente_value.value != "--":
                    try:
                        corriente_actual = float(corriente_value.value)
                        if corriente_actual > max_current:
                            alertas.append("Sobrecorriente ⚠️")
                    except ValueError:
                        pass
                
                if alertas:
                    estado_value.value = " | ".join(alertas)
                    estado_value.color = ft.Colors.RED
                else:
                    estado_value.value = "Óptimo ✅"
                    estado_value.color = ft.Colors.GREEN
            except ValueError:
                print("Error al convertir valor de voltaje actual")
        
        status_text.value = f"Límite de voltaje máximo actualizado a {max_voltage:.1f} V"
        
        # Publicar nuevo límite al dispositivo
        if mqtt_controller.connected:
            mqtt_controller.publish("medicion/VAC_MAX", f"{max_voltage:.1f}")
        
        page.update()

    def update_max_current(e):
        nonlocal max_current
        max_current = e.control.value
        max_current_value.value = f"{max_current:.1f} A"
        
        # Verificar si la corriente actual está fuera de límites
        if corriente_value.value != "--":
            try:
                valor_actual = float(corriente_value.value)
                alertas = []
                if valor_actual > max_current:
                    alertas.append("Sobrecorriente ⚠️")
                
                # Verificar también el voltaje
                if voltaje_value.value != "--":
                    try:
                        voltaje_actual = float(voltaje_value.value)
                        if voltaje_actual > max_voltage:
                            alertas.append("Sobrevoltaje ⚠️")
                        elif voltaje_actual < min_voltage:
                            alertas.append("Bajo Voltaje ⚠️")
                    except ValueError:
                        pass
                
                if alertas:
                    estado_value.value = " | ".join(alertas)
                    estado_value.color = ft.Colors.RED
                else:
                    estado_value.value = "Óptimo ✅"
                    estado_value.color = ft.Colors.GREEN
            except ValueError:
                print("Error al convertir valor de corriente actual")
        
        status_text.value = f"Límite de corriente máxima actualizado a {max_current:.1f} A"
        
        # Publicar nuevo límite al dispositivo
        if mqtt_controller.connected:
            mqtt_controller.publish("medicion/AMP_MAX", f"{max_current:.1f}")
        
        page.update()
        
    # Crear tarjetas para cada medición
    voltaje_card, update_voltaje = crear_tarjeta_medicion("Voltaje", voltaje_value, "V", ft.Icons.BOLT, ft.Colors.BLUE, ft.Colors.WHITE, [80, 220], "voltage", page)
    corriente_card, update_corriente = crear_tarjeta_medicion("Corriente", corriente_value, "A", ft.Icons.ELECTRIC_METER, ft.Colors.RED, ft.Colors.WHITE, [0, 40], "current", page)
    potencia_card, update_potencia = crear_tarjeta_medicion("Potencia", potencia_value, "kW", ft.Icons.POWER, ft.Colors.GREEN, ft.Colors.WHITE, [0, 8.8], "power", page, decimals=3, scale=1.0)
    frecuencia_card, update_frecuencia = crear_tarjeta_medicion("FP", frecuencia_value, "", ft.Icons.SPEED, ft.Colors.PURPLE, ft.Colors.WHITE, [0.0, 1.0], "frequency", page, decimals=2, scale=1.0)
    
    # Botones para la conexión y generación de datos de prueba
    btn_connect = ft.ElevatedButton(
        "Conectar",
        width=150,  # Set specific width
        height=40,  # Set specific height
        icon=ft.Icons.LINK,
        on_click=connect_mqtt,
        style=ft.ButtonStyle(
            color=ft.Colors.WHITE,
            bgcolor=ft.Colors.BLUE,
        )
    )
    
    btn_demo = ft.ElevatedButton(
        "Generar Datos de Prueba",
        icon=ft.Icons.DATA_OBJECT,
        on_click=generate_demo_data,
    )
    
    # Crear las vistas de pestañas
    tab_monitoreo = ft.Tab(
        text="Monitoreo",
        icon=ft.Icons.DASHBOARD,
        content=ft.Container(
            padding=10,
            content=ft.Column(
                controls=[
                    ft.Text("Panel de Monitoreo", size=20, weight=ft.FontWeight.BOLD),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            connection_status,
                            btn_connect,
                        ],
                    ),
                    ft.Container(
                        content=ft.Row(
                            alignment=ft.MainAxisAlignment.CENTER,
                            controls=[
                                ft.Text("Estado del Sistema: ", size=12),
                                estado_value,
                            ],
                        ),
                        bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.BLUE),
                        border_radius=10,
                        padding=5,
                    ),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=5,
                        controls=[
                            voltaje_card,
                            corriente_card,
                        ],
                    ),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=5,
                        controls=[
                            potencia_card,
                            frecuencia_card,
                        ],
                    ),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.CENTER,
                        controls=[btn_demo],
                    ),
                ],
            ),
        ),
    )
    
    tab_control = ft.Tab(
        text="Control y Alertas",
        icon=ft.Icons.SETTINGS,
        content=ft.Container(
            padding=10,
            content=ft.Column(
                controls=[
                    ft.Text("Panel de Control", size=24, weight=ft.FontWeight.BOLD),
                    
                    # Sección de límites de alarma
                    ft.Container(
                        padding=20,
                        border=ft.border.all(1, ft.Colors.BLUE_200),
                        border_radius=10,
                        content=ft.Column(
                            controls=[
                                ft.Text("Límites de Alarma", size=18, weight=ft.FontWeight.BOLD),
                                
                                # Voltaje mínimo
                                ft.Text("Voltaje Mínimo:", weight=ft.FontWeight.BOLD),
                                ft.Row([
                                    ft.Slider(
                                        min=80,
                                        max=220,
                                        value=min_voltage,
                                        divisions=140,
                                        label="{value}",
                                        on_change=update_min_voltage,
                                        expand=True,
                                    ),
                                    min_voltage_value,
                                ]),
                                
                                # Voltaje máximo
                                ft.Text("Voltaje Máximo:", weight=ft.FontWeight.BOLD),
                                ft.Row([
                                    ft.Slider(
                                        min=80,
                                        max=220,
                                        value=max_voltage,
                                        divisions=140,
                                        label="{value}",
                                        on_change=update_max_voltage,
                                        expand=True,
                                    ),
                                    max_voltage_value,
                                ]),
                                
                                # Corriente máxima
                                ft.Text("Corriente Máxima:", weight=ft.FontWeight.BOLD),
                                ft.Row([
                                    ft.Slider(
                                        min=20,
                                        max=60,
                                        value=max_current,
                                        divisions=40,
                                        label="{value}",
                                        on_change=update_max_current,
                                        expand=True,
                                    ),
                                    max_current_value,
                                ]),
                            ],
                        ),
                    ),
                    
                    # Sección de control de carga
                    ft.Container(
                        padding=20,
                        margin=ft.margin.only(top=20),
                        border=ft.border.all(1, ft.Colors.GREEN_200),
                        border_radius=10,
                        content=ft.Column(
                            controls=[
                                ft.Text("Control de Carga", size=18, weight=ft.FontWeight.BOLD),
                                ft.Row(
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    controls=[
                                        ft.Text("Estado de la carga:"),
                                        estado_carga,
                                    ],
                                ),
                                ft.Row(
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    controls=[
                                        ft.Text("Activar/Desactivar:"),
                                        switch_carga,
                                    ],
                                ),
                                ft.ElevatedButton(
                                    "Enviar Comando",
                                    icon=ft.Icons.SEND,
                                    on_click=send_control_command,
                                    width=200,
                                ),
                                status_text,
                            ],
                        ),
                    ),
                ],
            ),
        ),
    )

    voltaje_chart = ft.LineChart(
        data_series=[
            ft.LineChartData(
                data_points=[],
                stroke_width=2,
                color=ft.Colors.BLUE,
            )
        ],
        min_x=0,
        max_x=MAX_DATA_POINTS,
        min_y=0,
        max_y=220,
        expand=True,
        height=150,
    )

    corriente_chart = ft.LineChart(
        data_series=[
            ft.LineChartData(
                data_points=[],
                stroke_width=2,
                color=ft.Colors.RED,
            )
        ],
        min_x=0,
        max_x=MAX_DATA_POINTS,
        min_y=0,
        max_y=40,
        expand=True,
        height=150,
    )

    tab_graficas = ft.Tab(
        text="Gráficas",
        icon=ft.Icons.SHOW_CHART,
        content=ft.Container(
            padding=10,
            content=ft.Column(
                spacing=10,
                controls=[
                    ft.Text("Panel de Gráficas", size=24, weight=ft.FontWeight.BOLD),
                    ft.Container(
                        padding=10,
                        border=ft.border.all(1, ft.Colors.BLUE_200),
                        border_radius=10,
                        content=ft.Column([
                            ft.Text("Voltaje (V)", size=16, weight=ft.FontWeight.BOLD),
                            voltaje_chart,
                        ], spacing=5),
                    ),
                    ft.Container(
                        padding=10,
                        border=ft.border.all(1, ft.Colors.RED_200),
                        border_radius=10,
                        content=ft.Column([
                            ft.Text("Corriente (A)", size=16, weight=ft.FontWeight.BOLD),
                            corriente_chart,
                        ], spacing=5),
                    ),
                ],
            ),
        ),
    )

    # Crear pestaña de Grafana
    tab_grafana = ft.Tab(
        text="Grafana",
        icon=ft.Icons.ANALYTICS,
        content=ft.Container(
            padding=10,
            content=ft.Column(
                controls=[
                    ft.Text("Paneles de Grafana", size=24, weight=ft.FontWeight.BOLD),
                    
                    # Panel 1: Diagrama tipo pastel
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Text("Diagrama tipo pastel", size=18, weight=ft.FontWeight.BOLD),
                                ft.Container(
                                    content=ft.Image(
                                        src="https://olivierpeguero.grafana.net/d-solo/behnfg1more2oc/pruebas-de-graficos?orgId=1&from=1746488974464&to=1746489274464&timezone=browser&src=hg_notification_free&panelId=6&__feature.dashboardSceneSolo",
                                        width=450,
                                        height=200,
                                        fit=ft.ImageFit.CONTAIN,
                                    ),
                                    border=ft.border.all(1, ft.Colors.BLUE_200),
                                    border_radius=10,
                                    padding=10,
                                ),
                                ft.ElevatedButton(
                                    "Abrir Diagrama Pastel",
                                    icon=ft.Icons.PIE_CHART,
                                    on_click=lambda e: page.launch_url(
                                        "https://olivierpeguero.grafana.net/d-solo/behnfg1more2oc/pruebas-de-graficos?orgId=1&from=1746488974464&to=1746489274464&timezone=browser&src=hg_notification_free&panelId=6&__feature.dashboardSceneSolo",
                                        web_window_name="_blank"
                                    ),
                                    style=ft.ButtonStyle(
                                        color=ft.Colors.WHITE,
                                        bgcolor=ft.Colors.BLUE,
                                    ),
                                ),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        padding=10,
                    ),
                    
                    # Panel 2: Diagrama Voltaje vs Corriente
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Text("Diagrama Voltaje vs Corriente", size=18, weight=ft.FontWeight.BOLD),
                                ft.Container(
                                    content=ft.Image(
                                        src="https://olivierpeguero.grafana.net/d-solo/behnfg1more2oc/pruebas-de-graficos?orgId=1&from=1746488974464&to=1746489274464&timezone=browser&src=hg_notification_free&panelId=4&__feature.dashboardSceneSolo",
                                        width=450,
                                        height=200,
                                        fit=ft.ImageFit.CONTAIN,
                                    ),
                                    border=ft.border.all(1, ft.Colors.BLUE_200),
                                    border_radius=10,
                                    padding=10,
                                ),
                                ft.ElevatedButton(
                                    "Abrir Diagrama V vs I",
                                    icon=ft.Icons.SHOW_CHART,
                                    on_click=lambda e: page.launch_url(
                                        "https://olivierpeguero.grafana.net/d-solo/behnfg1more2oc/pruebas-de-graficos?orgId=1&from=1746488974464&to=1746489274464&timezone=browser&src=hg_notification_free&panelId=4&__feature.dashboardSceneSolo",
                                        web_window_name="_blank"
                                    ),
                                    style=ft.ButtonStyle(
                                        color=ft.Colors.WHITE,
                                        bgcolor=ft.Colors.BLUE,
                                    ),
                                ),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        padding=10,
                    ),
                    
                    # Panel 3: Dashboard de mediciones en tiempo real
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Text("Dashboard de mediciones en tiempo real", size=18, weight=ft.FontWeight.BOLD),
                                ft.Container(
                                    content=ft.Image(
                                        src="https://olivierpeguero.grafana.net/d-solo/behnfg1more2oc/pruebas-de-graficos?orgId=1&from=1746488974464&to=1746489274464&timezone=browser&src=hg_notification_free&theme=light&panelId=7&__feature.dashboardSceneSolo",
                                        width=450,
                                        height=200,
                                        fit=ft.ImageFit.CONTAIN,
                                    ),
                                    border=ft.border.all(1, ft.Colors.BLUE_200),
                                    border_radius=10,
                                    padding=10,
                                ),
                                ft.ElevatedButton(
                                    "Abrir Dashboard",
                                    icon=ft.Icons.DASHBOARD,
                                    on_click=lambda e: page.launch_url(
                                        "https://olivierpeguero.grafana.net/d-solo/behnfg1more2oc/pruebas-de-graficos?orgId=1&from=1746488974464&to=1746489274464&timezone=browser&src=hg_notification_free&theme=light&panelId=7&__feature.dashboardSceneSolo",
                                        web_window_name="_blank"
                                    ),
                                    style=ft.ButtonStyle(
                                        color=ft.Colors.WHITE,
                                        bgcolor=ft.Colors.BLUE,
                                    ),
                                ),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        padding=10,
                    ),
                ],
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            ),
        ),
    )

    # Función para actualizar las gráficas
    def update_charts():
        while mqtt_connected:
            try:
                # Actualizar gráficas cada 100ms
                if voltaje_chart.data_series[0].data_points:
                    voltaje_chart.update()
                if corriente_chart.data_series[0].data_points:
                    corriente_chart.update()
                time.sleep(0.1)
            except Exception as e:
                print(f"Error al actualizar gráficas: {str(e)}")
    
    # Iniciar hilo de actualización de gráficas
    chart_update_thread = None
    
    def on_tab_change(e):
        nonlocal chart_update_thread
        if e.control.selected_index == 2:  # Tab de gráficas
            if chart_update_thread is None or not chart_update_thread.is_alive():
                chart_update_thread = threading.Thread(target=update_charts)
                chart_update_thread.daemon = True
                chart_update_thread.start()
    
    # Configurar las pestañas en la interfaz
    tabs = ft.Tabs(
        selected_index=0,
        animation_duration=300,
        tabs=[tab_monitoreo, tab_control, tab_graficas, tab_grafana],
        expand=True,
        on_change=on_tab_change
    )
    
    # Añadir las pestañas a la página
    page.add(tabs)

    # Manejar el cierre de la ventana para detener los hilos MQTT
    def on_window_event(e):
        if e.data == "close":
            if mqtt_connected:
                mqtt_controller.disconnect()
                
    page.on_window_event = on_window_event

    def actualizar_estado_sistema():
        # Verificar voltaje actual
        if voltaje_value.value != "--":
            try:
                valor_actual = float(voltaje_value.value)
                alertas_activas["bajo_voltaje"] = valor_actual < min_voltage
                alertas_activas["sobrevoltaje"] = valor_actual > max_voltage
            except ValueError:
                pass
        
        # Verificar corriente actual
        if corriente_value.value != "--":
            try:
                valor_actual = float(corriente_value.value)
                alertas_activas["sobrecorriente"] = valor_actual > max_current
            except ValueError:
                pass
        
        # Construir mensaje de estado
        alertas = []
        if alertas_activas["bajo_voltaje"]:
            alertas.append("Bajo Voltaje ⚠️")
        if alertas_activas["sobrevoltaje"]:
            alertas.append("Sobrevoltaje ⚠️")
        if alertas_activas["sobrecorriente"]:
            alertas.append("Sobrecorriente ⚠️")
        
        # Actualizar estado en la interfaz
        if alertas:
            estado_value.value = " | ".join(alertas)
            estado_value.color = ft.Colors.RED
        else:
            estado_value.value = "Óptimo ✅"
            estado_value.color = ft.Colors.GREEN
        
        page.update()

# Iniciar la aplicación
if __name__ == "__main__":
    ft.app(target=main)