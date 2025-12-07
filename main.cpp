#include <Arduino.h>
#include <EEPROM.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ZMPT101B.h>

// Definición de pines de sensores y control
#define VOLTAJE_PIN 1    // Sensor ZMPT101B
#define CORRIENTE_PIN 2  // Sensor SCT013
#define SCR_PIN 3        // Control de carga
#define LED_SOBREVOLT_PIN 4  // LED sobrevoltaje
#define LED_BAJOVOLT_PIN 5   // LED bajo voltaje
#define LED_SOBRECORR_PIN 12  // LED sobrecorriente (Moved from 9 to 12 to free 9 for ZC)

// Pines para cruce por cero (Factor de Potencia)
#define PIN_ZC_VOLTAGE 9
#define PIN_ZC_CURRENT 18

// Variables del sistema
const char* ssid = "Altice_31B5";
const char* password = "f22n29w02";
float VAC_MIN = 105.0;
float VAC_MAX = 125.0;
float AMP_MAX = 20.0;
bool wifi_enabled = true;

// Variables de medición reales
float voltage_ac = 0.0;
float current_ac = 0.0;
float power_kw = 0.0;
float frequency = 60.0; // Fija en 60 Hz

// Variables de control
bool carga_habilitada_usuario = true;  // Control remoto por MQTT
bool carga_activa = false;             // Estado actual de la carga
String estado_sistema = "INACTIVO";

// Variables para temporización sin delay()
unsigned long lastMeasurementTime = 0;
unsigned long lastMQTTPublishTime = 0;
const unsigned long MEASUREMENT_INTERVAL = 1000;  // 1 segundo
const unsigned long MQTT_PUBLISH_INTERVAL = 1000; // 1 segundo

// Direcciones EEPROM
#define EEPROM_SIZE 512
#define ADDR_VAC_MIN 0
#define ADDR_VAC_MAX 4
#define ADDR_AMP_MAX 8
#define ADDR_WIFI_ENABLED 12

// Instancias de sensores
ZMPT101B voltageSensor(VOLTAJE_PIN, 60.0);  // 60Hz para México

// Variables para filtrado de corriente
const int CURRENT_AVG_SIZE = 10;
float currentHistory[CURRENT_AVG_SIZE];
int currentHistoryIndex = 0;
float currentAverage = 0.0;
float current_offset = 0.0;
bool offset_calculated = false;

// Variables para filtrado de voltaje
const int VOLTAGE_AVG_SIZE = 20;
float voltageHistory[VOLTAGE_AVG_SIZE];
int voltageHistoryIndex = 0;
float voltageAverage = 0.0;
const float SENSITIVITY = 518.0f;

// ADC/corriente
const float ADC_VREF = 3.3f;
const int ADC_BITS = 12;
const float V_PER_AMP = 0.3f;
const unsigned long CURRENT_SAMPLE_MS = 350;
const float CURRENT_NOISE_THRESHOLD = 0.03f;
const float CURRENT_CAL_GAIN = 13.0f;

// Variables para Factor de Potencia
volatile unsigned long zc_time_voltage = 0;
volatile unsigned long zc_time_current = 0;
float power_factor = 0.0;



// Tópicos MQTT
const char* topicVoltaje = "medicion/voltaje";
const char* topicCorriente = "medicion/corriente";
const char* topicFrecuencia = "medicion/frecuencia";
const char* topicPotencia = "medicion/potencia";
const char* topicEstado = "medicion/estado";
const char* topicControlReset = "control/reset";
const char* topicVACMin = "medicion/VAC_MIN";
const char* topicVACMax = "medicion/VAC_MAX";
const char* topicAMPMax = "medicion/AMP_MAX";
const char* topicFactorPotencia = "medicion/factor_potencia";

// Configuración MQTT
const char* mqtt_server = "broker.emqx.io";
const int mqtt_port = 1883;
WiFiClient espClient;
PubSubClient client(espClient);
bool mqtt_connected = false;

// Prototypes
void controlCargaYAlarmas();
void reconnectMQTT();
void publishMQTTData();
float medirCorrienteRMS(uint8_t pin = CORRIENTE_PIN);
void calculatePowerFactor();

// ISRs para cruce por cero
void IRAM_ATTR onZcVoltage() {
  zc_time_voltage = micros();
}

void IRAM_ATTR onZcCurrent() {
  zc_time_current = micros();
}

// Funciones de EEPROM
void saveToEEPROM() {
  EEPROM.writeFloat(ADDR_VAC_MIN, VAC_MIN);
  EEPROM.writeFloat(ADDR_VAC_MAX, VAC_MAX);
  EEPROM.writeFloat(ADDR_AMP_MAX, AMP_MAX);
  EEPROM.writeBool(ADDR_WIFI_ENABLED, wifi_enabled);
  EEPROM.commit();
}

void loadFromEEPROM() {
  VAC_MIN = EEPROM.readFloat(ADDR_VAC_MIN);
  VAC_MAX = EEPROM.readFloat(ADDR_VAC_MAX);
  AMP_MAX = EEPROM.readFloat(ADDR_AMP_MAX);
  wifi_enabled = EEPROM.readBool(ADDR_WIFI_ENABLED);
  
  // Si los valores son inválidos (primera ejecución), usar valores por defecto
  if (isnan(VAC_MIN) || isnan(VAC_MAX) || isnan(AMP_MAX)) {
    VAC_MIN = 105.0;
    VAC_MAX = 125.0;
    AMP_MAX = 20.0;
    wifi_enabled = true;
    saveToEEPROM();
  }
}

// Función para leer voltaje AC (basada en voltaje_simple.txt)
float readVoltageAC() {
  // Leer el voltaje RMS directamente del sensor
  float voltage = voltageSensor.getRmsVoltage();
  
  // Actualizar el historial de voltajes
  voltageHistory[voltageHistoryIndex] = voltage;
  voltageHistoryIndex = (voltageHistoryIndex + 1) % VOLTAGE_AVG_SIZE;
  
  // Calcular el promedio
  float sum = 0.0;
  for (int i = 0; i < VOLTAGE_AVG_SIZE; i++) {
    sum += voltageHistory[i];
  }
  voltageAverage = sum / VOLTAGE_AVG_SIZE;
  
  return voltageAverage;
}

// Función para leer corriente AC (sensor SCT013 30A/1V)
float readCurrentAC() {
  float current_sum_sq = 0.0;
  int sample_count = 0;
  unsigned long start_time = millis();
  
  // Muestrear durante 200ms (aprox 12 ciclos a 60Hz)
  while (millis() - start_time < 200) {
    // Leer ADC y convertir a voltaje (ESP32 12-bit, 3.3V)
    float adc_voltage = (analogRead(CORRIENTE_PIN) * 3.3) / 4095.0;
    
    // Convertir voltaje a corriente (relación 30A/1V -> Factor 30)
    float current_inst = adc_voltage * 30.0; 
    
    // Acumular cuadrados
    current_sum_sq += sq(current_inst);
    sample_count++;
  }
  
  if (sample_count > 0) {
    // Compensar semiciclos negativos (multiplicar energía por 2)
    current_sum_sq = current_sum_sq * 2.0; 
    
    // Calcular RMS
    float current_rms = sqrt(current_sum_sq / sample_count);
    
    // Filtrar ruido bajo (ajustable)
    if (current_rms < 0.15) current_rms = 0.0;
    
    // Actualizar historial para promedio móvil
    currentHistory[currentHistoryIndex] = current_rms;
    currentHistoryIndex = (currentHistoryIndex + 1) % CURRENT_AVG_SIZE;
    
    // Calcular promedio móvil
    float sum = 0.0;
    for (int i = 0; i < CURRENT_AVG_SIZE; i++) {
      sum += currentHistory[i];
    }
    currentAverage = sum / CURRENT_AVG_SIZE;
    
    return currentAverage;
  }
  
  return 0.0;
}

float medirCorrienteRMS(uint8_t pin) {
  static bool adc_cfg = false;
  if (!adc_cfg) {
    analogReadResolution(ADC_BITS);
    analogSetPinAttenuation(pin, ADC_11db);
    adc_cfg = true;
  }
  
  unsigned long start = millis();
  double sum = 0.0;
  double sumsq = 0.0;
  uint32_t n = 0;
  
  while (millis() - start < CURRENT_SAMPLE_MS) {
    float v = analogRead(pin) * (ADC_VREF / 4095.0f);
    sum += v;
    sumsq += v * v;
    n++;
  }
  
  if (n == 0) return 0.0f;
  
  double mean = sum / (double)n;
  double mean_sq = sumsq / (double)n;
  double v_rms = sqrt(fmax(0.0, mean_sq - mean * mean)); // Restaurado AC coupling para RMS real
  
  // Calcular corriente bruta con ganancias
  float i_rms = (float)(v_rms / V_PER_AMP) * CURRENT_CAL_GAIN;
  
  // Auto-calibración del offset al inicio
  if (!offset_calculated) {
    if (i_rms < 0.65f) { // Umbral aumentado para nuevo offset
      current_offset = i_rms;
    }
    offset_calculated = true;
  }
  
  // Corrección de offset por software (Ajuste solicitado)
  // Si la autocalibración falló o dio 0, aplicamos el offset fijo observado
  if (current_offset < 0.01f) {
      current_offset = 0.36f; // Nuevo offset escalado
  }
  
  // Aplicar offset
  i_rms -= current_offset;
  if (i_rms < 0.0f) i_rms = 0.0f;
  
  // Aplicar umbral de ruido reducido
  if (i_rms < CURRENT_NOISE_THRESHOLD) i_rms = 0.0f;
  
  // Promedio móvil
  currentHistory[currentHistoryIndex] = i_rms;
  currentHistoryIndex = (currentHistoryIndex + 1) % CURRENT_AVG_SIZE;
  
  float sum_avg = 0.0;
  for (int i = 0; i < CURRENT_AVG_SIZE; i++) {
    sum_avg += currentHistory[i];
  }
  currentAverage = sum_avg / CURRENT_AVG_SIZE;
  
  return currentAverage;
}

// =============================================================== 
// FACTOR DE POTENCIA PROFESIONAL (ESTILO MEDIDOR COMERCIAL) 
// Sin alterar el funcionamiento actual del sistema 
// =============================================================== 
void calculatePowerFactor() { 

    // Usa tus RMS y potencia existentes 
    float V = voltage_ac;      // Vrms calculado por tu sistema 
    float I = current_ac;      // Irms calculado por tu sistema 
    // float P = real_power;   // Nota: No hay medición directa de P, se usa ZC
    float S = (V * I);         // Potencia Aparente

    // -------------------------------------------- 
    // 0. PROTECCIÓN – SIN CARGA = FP = 0 ESTABLE 
    // -------------------------------------------- 
    // Se usa S < 0.5 en lugar de P porque P no está disponible
    if (V < 10.0f || I < 0.03f || S < 0.5f) { 
        power_factor = 0.0f; 
        return; 
    } 

    // -------------------------------------------- 
    // 1. FP INSTANTÁNEO REAL (MÉTODO ZC) 
    // -------------------------------------------- 
    // Usamos cruce por cero porque es la única forma de medir FP
    // en este hardware sin muestreo simultáneo V/I.
    noInterrupts();
    unsigned long t_v = zc_time_voltage;
    unsigned long t_c = zc_time_current;
    interrupts();

    long delta_t = t_c - t_v;
    float period = 16666.6f; 
    float phase_deg = ((float)delta_t / period) * 360.0f;
    float phase_rad = phase_deg * PI / 180.0f;
    
    // fp_inst = cos(fase) equivale a P/S
    float fp_inst = cos(phase_rad);

    // Asegurar magnitud positiva
    if (fp_inst < 0.0f) fp_inst = -fp_inst;

    // Clamp 
    if (fp_inst < 0.0f) fp_inst = 0.0f; 
    if (fp_inst > 1.0f) fp_inst = 1.0f; 

    // -------------------------------------------- 
    // 2. FILTRO EXPONENCIAL (EMA) 
    // -------------------------------------------- 
    static float fp_ema = 0.0f; 
    const float alpha = 0.25f;   // filtrado suave 
    fp_ema = alpha * fp_inst + (1.0f - alpha) * fp_ema; 

    // -------------------------------------------- 
    // 3. PROMEDIO MÓVIL (MUY ESTABLE) 
    // -------------------------------------------- 
    const int N = 15; 
    static float buffer[N]; // Buffer estático
    static int idx = 0; 
    static float suma = 0.0f; 
    static bool initialized = false;

    // Inicialización segura del buffer
    if (!initialized) {
        for (int i = 0; i < N; i++) buffer[i] = 0.0f;
        fp_ema = 0.0f; // Inicializar EMA también
        initialized = true;
    }

    // Quitar la muestra más vieja 
    suma -= buffer[idx]; 

    // Insertar la nueva filtrada 
    buffer[idx] = fp_ema; 

    // Sumar 
    suma += fp_ema; 

    idx++; 
    if (idx >= N) idx = 0; 

    // Promedio final 
    float fp_final = suma / (float)N; 

    // Clamp final 
    if (fp_final < 0.0f) fp_final = 0.0f; 
    if (fp_final > 1.0f) fp_final = 1.0f; 

    // -------------------------------------------- 
    // 4. ASIGNAR AL SISTEMA 
    // -------------------------------------------- 
    power_factor = fp_final; 
}

// Función para realizar mediciones
void performMeasurements() {
  unsigned long currentTime = millis();
  
  if (currentTime - lastMeasurementTime >= MEASUREMENT_INTERVAL) {
    // Leer sensores
    voltage_ac = readVoltageAC();
    current_ac = medirCorrienteRMS();
    
    // Calcular Factor de Potencia
    calculatePowerFactor();
    
    // --- SECCIÓN DE CORRECCIÓN (Deadband y FP) ---
    #define I_THRESHOLD 0.03f
    
    // Si la corriente es menor al umbral, forzar a cero
    if (current_ac < I_THRESHOLD) {
      current_ac = 0.0f;
      // La potencia se hará 0.0 automáticamente abajo
    }
    
    // Protección de Factor de Potencia
    if (current_ac == 0.0f || voltage_ac == 0.0f) {
      power_factor = 0.0f;
    } else {
      // Asegurar rango válido [0.0 - 1.0] para el FP calculado por ZC
      if (power_factor < 0.0f) power_factor = 0.0f;
      if (power_factor > 1.0f) power_factor = 1.0f;
    }
    // ---------------------------------------------
    
    // Calcular potencia en kW
    // Nota: Si se quisiera usar PF: power_active = V * I * PF / 1000.0
    // Por ahora mantenemos cálculo aparente original o ajustamos si se pide.
    // El usuario dijo "No alterar el sistema de medición... potencia".
    // Mantenemos power_kw = V*I/1000 (Potencia Aparente kVA, aunque la variable dice kw)
    power_kw = (voltage_ac * current_ac) / 1000.0;
    
    // Controlar carga y LEDs basado en las condiciones
    controlCargaYAlarmas();
    
    lastMeasurementTime = currentTime;
  }
}

// Función para controlar carga y alarmas
void controlCargaYAlarmas() {
  bool bajo_voltaje = voltage_ac < VAC_MIN;
  bool sobre_voltaje = voltage_ac > VAC_MAX;
  bool sobre_corriente = current_ac > AMP_MAX;
  
  // Controlar LEDs de alarma con histéresis
  if (bajo_voltaje) {
    digitalWrite(LED_BAJOVOLT_PIN, HIGH);
    Serial.println("Alarma: Bajo voltaje detectado");
  } else if (voltage_ac > (VAC_MIN + 2.0)) { // Histéresis de 2V
    digitalWrite(LED_BAJOVOLT_PIN, LOW);
  }
  
  if (sobre_voltaje) {
    digitalWrite(LED_SOBREVOLT_PIN, HIGH);
    Serial.println("Alarma: Sobrevoltaje detectado");
  } else if (voltage_ac < (VAC_MAX - 2.0)) { // Histéresis de 2V
    digitalWrite(LED_SOBREVOLT_PIN, LOW);
  }
  
  if (sobre_corriente) {
    digitalWrite(LED_SOBRECORR_PIN, HIGH);
    Serial.println("Alarma: Sobrecorriente detectado");
  } else if (current_ac < (AMP_MAX - 0.5)) { // Histéresis de 0.5A
    digitalWrite(LED_SOBRECORR_PIN, LOW);
  }
  
  // Determinar si se puede activar la carga
  bool condiciones_ok = !bajo_voltaje && !sobre_voltaje && !sobre_corriente;
  
  // Activar/desactivar carga basado en condiciones y control de usuario
  if (condiciones_ok && carga_habilitada_usuario) {
    digitalWrite(SCR_PIN, HIGH);
    carga_activa = true;
    Serial.println("Carga activada - Condiciones OK");
  } else {
    digitalWrite(SCR_PIN, LOW);
    carga_activa = false;
    if (!condiciones_ok) {
      Serial.println("Carga desactivada - Condiciones fuera de rango");
    }
  }
  
  // Actualizar estado del sistema
  if (!carga_habilitada_usuario) {
    estado_sistema = "DESHABILITADO";
  } else if (bajo_voltaje) {
    estado_sistema = "BAJO_VOLT";
  } else if (sobre_voltaje) {
    estado_sistema = "SOBREVOLT";
  } else if (sobre_corriente) {
    estado_sistema = "SOBRECORR";
  } else if (carga_activa) {
    estado_sistema = "ACTIVO";
  } else {
    estado_sistema = "INACTIVO";
  }
  
  // Publicar estado si está conectado a MQTT
  if (client.connected()) {
    client.publish("medicion/estado", estado_sistema.c_str());
  }
}

// Función para generar ID de cliente aleatorio
String generateClientId() {
  String clientId = "ESP32Client-";
  clientId += String(random(0xffff), HEX);
  return clientId;
}

// Callback para mensajes MQTT recibidos
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  // Convertir payload a string
  char message[length + 1];
  memcpy(message, payload, length);
  message[length] = '\0';
  String strMessage = String(message);
  
  Serial.print("Mensaje recibido en tópico: ");
  Serial.println(topic);
  Serial.print("Contenido: ");
  Serial.println(strMessage);
  
  // Procesar mensaje según el tópico
  if (strcmp(topic, "control/reset") == 0) {
    if (strMessage == "1" || strMessage == "ON") {
      carga_habilitada_usuario = true;
      digitalWrite(SCR_PIN, HIGH);
      Serial.println("Carga habilitada por usuario");
    } else if (strMessage == "0" || strMessage == "OFF") {
      carga_habilitada_usuario = false;
      digitalWrite(SCR_PIN, LOW);
      Serial.println("Carga deshabilitada por usuario");
    }
  }
  else if (strcmp(topic, "medicion/VAC_MIN") == 0) {
    float newValue = strMessage.toFloat();
    if (newValue > 0 && newValue < VAC_MAX) {
      VAC_MIN = newValue;
      saveToEEPROM();
      Serial.print("Nuevo VAC_MIN: ");
      Serial.println(VAC_MIN);
    }
  }
  else if (strcmp(topic, "medicion/VAC_MAX") == 0) {
    float newValue = strMessage.toFloat();
    if (newValue > VAC_MIN && newValue < 250) {
      VAC_MAX = newValue;
      saveToEEPROM();
      Serial.print("Nuevo VAC_MAX: ");
      Serial.println(VAC_MAX);
    }
  }
  else if (strcmp(topic, "medicion/AMP_MAX") == 0) {
    float newValue = strMessage.toFloat();
    if (newValue > 0 && newValue < 100) {
      AMP_MAX = newValue;
      saveToEEPROM();
      Serial.print("Nuevo AMP_MAX: ");
      Serial.println(AMP_MAX);
    }
  }
}

// Función para conectar al broker MQTT
void reconnectMQTT() {
  if (!client.connected()) {
    String clientId = generateClientId();
    
    if (client.connect(clientId.c_str())) {
      mqtt_connected = true;
      
      // Suscribirse a los tópicos
      client.subscribe("control/reset");
      client.subscribe("medicion/VAC_MIN");
      client.subscribe("medicion/VAC_MAX");
      client.subscribe("medicion/AMP_MAX");
    }
  }
}

// Función para publicar datos MQTT
void publishMQTTData() {
  if (!client.connected()) return;
  
  unsigned long currentTime = millis();
  
  if (currentTime - lastMQTTPublishTime >= MQTT_PUBLISH_INTERVAL) {
    char buffer[32];
    
    // Publicar voltaje
    sprintf(buffer, "%.2f", voltage_ac);
    client.publish("medicion/voltaje", buffer);
    
    // Publicar corriente
    sprintf(buffer, "%.2f", current_ac);
    client.publish("medicion/corriente", buffer);
    
    // Publicar frecuencia
    sprintf(buffer, "%.1f", frequency);
    client.publish("medicion/frecuencia", buffer);
    
    // Publicar potencia
    sprintf(buffer, "%.3f", power_kw);
    client.publish("medicion/potencia", buffer);
    
    // Publicar factor de potencia
    sprintf(buffer, "%.2f", power_factor);
    client.publish("medicion/factor_potencia", buffer);
    
    // Publicar estado
    client.publish("medicion/estado", estado_sistema.c_str());
    
    lastMQTTPublishTime = currentTime;
  }
}

void setup() {
  Serial.begin(115200);
  
  // Inicializar EEPROM
  EEPROM.begin(EEPROM_SIZE);
  loadFromEEPROM();
  
  // Inicializar pines de control y LEDs
  pinMode(SCR_PIN, OUTPUT);
  pinMode(LED_BAJOVOLT_PIN, OUTPUT);
  pinMode(LED_SOBREVOLT_PIN, OUTPUT);
  pinMode(LED_SOBRECORR_PIN, OUTPUT);
  
  // Configurar pines de cruce por cero
  pinMode(PIN_ZC_VOLTAGE, INPUT_PULLUP);
  pinMode(PIN_ZC_CURRENT, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_ZC_VOLTAGE), onZcVoltage, RISING);
  attachInterrupt(digitalPinToInterrupt(PIN_ZC_CURRENT), onZcCurrent, RISING);
  
  // Apagar todos los LEDs y SCR inicialmente
  digitalWrite(SCR_PIN, LOW);
  digitalWrite(LED_BAJOVOLT_PIN, LOW);
  digitalWrite(LED_SOBREVOLT_PIN, LOW);
  digitalWrite(LED_SOBRECORR_PIN, LOW);
  
  // Configurar resolución ADC
  analogReadResolution(12);
  
  // Inicializar sensor de voltaje
  voltageSensor.setSensitivity(SENSITIVITY);
  
  // Inicializar historiales de filtrado
  for (int i = 0; i < VOLTAGE_AVG_SIZE; i++) {
    voltageHistory[i] = 0.0;
  }
  for (int i = 0; i < CURRENT_AVG_SIZE; i++) {
    currentHistory[i] = 0.0;
  }
  
  // Inicializar WiFi si está habilitado
  if (wifi_enabled) {
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid, password);
  }
  
  // Configurar MQTT
  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(mqttCallback);
  
  Serial.println("Sistema iniciado - Medición AC en tiempo real");
}

void loop() {
  // Realizar mediciones en tiempo real (sin bloquear)
  performMeasurements();
  
  // Manejar WiFi y MQTT
  if (wifi_enabled) {
    if (WiFi.status() == WL_CONNECTED) {
      if (!client.connected()) {
        reconnectMQTT();
      }
      client.loop();
      
      // Publicar datos MQTT
      publishMQTTData();
    }
  }
}
