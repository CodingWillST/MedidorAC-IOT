⚡ Proyecto: Medidor AC con ESP32 + Dashboard en Flet (Python)

Este proyecto implementa un sistema completo de medición de parámetros eléctricos AC utilizando un ESP32 como unidad de adquisición y una aplicación multiplataforma desarrollada en Flet (Python) para visualizar los datos en tiempo real mediante el protocolo MQTT.

El sistema permite monitorear:

Voltaje RMS

Corriente RMS

Frecuencia

Potencia activa

Factor de potencia

Estado del sistema (conectado/desconectado)

🧩 Características del Proyecto
✔ 1. Medidor AC con ESP32 (Firmware)

El ESP32 se encarga de la adquisición de datos y su publicación mediante MQTT.

Funciones del firmware:

Cálculo de:

Voltaje RMS

Corriente RMS

Frecuencia AC

Potencia activa

Factor de potencia

Publicación periódica por MQTT.

Suscripción a comandos remotos:

control/reset

Reconexión automática al broker.

Reportes de estado.

Tópicos MQTT utilizados:
Parámetro	Tópico
Voltaje RMS	medicion/voltaje
Corriente RMS	medicion/corriente
Frecuencia / Factor de potencia	medicion/factor_potencia
Potencia	medicion/potencia
Estado del sistema	medicion/estado
Control remoto	control/reset
✔ 2. Dashboard en Python (Flet)

La aplicación está hecha en Flet, es totalmente multiplataforma (Windows, Linux, Android, Web) y utiliza un cliente MQTT implementado desde cero, sin librerías externas.

Características de la App:

Cliente MQTT basado en sockets (CONNECT, SUBSCRIBE, PUBLISH, PINGREQ).

Reconexión automática en segundo plano.

Gráficas dinámicas con hasta 50 puntos recientes.

Indicadores en tiempo real:

Voltaje

Corriente

Frecuencia

Potencia

Factor de potencia

Panel de estado del sistema.

Envío de comandos a ESP32 (ej. Reset).

Arquitectura Interna:

Clase MQTTClient:

Maneja conexión, decodificación de paquetes y suscripciones.

Implementa KeepAlive y PING.

Corre en múltiples hilos.

UI en Flet:

Aplicación responsive.

Actualización automática al recibir datos.

Manejo de gráficas y paneles de control.

🛰 Arquitectura del Sistema
ESP32 ───► MQTT Broker (broker.emqx.io) ───► App Flet (Dashboard)
    │                                              ▲
    └────── Control remoto (reset) ◄───────────────┘

📂 Estructura del Repositorio
/firmware_esp32
    ├── main.cpp / main.ino
    ├── lecturas_sensores/
    ├── mqtt/
    └── utilidades/

/app_flet
    ├── main.py
    ├── mqtt_client.py
    ├── ui_components/
    └── utils/

🚀 Instalación y Ejecución
📌 Requisitos
Firmware ESP32:

PlatformIO / Arduino IDE

Librerías ADC o sensor según hardware

App en Flet:
pip install flet

▶ Ejecutar la App

Desde /app_flet:

python main.py

⚙ Configuración
Parámetros MQTT:

Broker: broker.emqx.io

Puerto: 1883

KeepAlive: 30s

Puedes modificarlos directamente en los archivos:

firmware_esp32/mqtt_config.h

app_flet/main.py

🧪 Sensores AC Recomendados

ZMPT101B → medición de voltaje AC

SCT-013 → medición de corriente AC

Acondicionamiento de señal adecuado (offset + limitación)

📈 Ejemplo de Flujo de Datos

El ESP32 mide las señales AC.

Publica los valores en los tópicos MQTT.

La app Flet está suscrita y recibe los datos.

Los muestra en gráficas y tarjetas de información.

El usuario puede enviar comandos al ESP32.

💡 Objetivo del Proyecto

Crear un sistema robusto, portable y eficiente para:

✔ Medir parámetros eléctricos AC
✔ Enviar datos en tiempo real por MQTT
✔ Visualizarlos en una app moderna y responsive
✔ Permitir control remoto del medidor
✔ Ser compatible con cualquier plataforma

📜 Licencia

Este proyecto se entrega bajo licencia MIT. Puedes modificarlo y distribuirlo libremente.

🙌 Autor

Willy Infante
Estudiante de Ingeniería Electrónica – Proyecto Lab Electrónica
Apasionado por IoT, programación y sistemas embebidos.

