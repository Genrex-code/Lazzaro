// ESP32: mapa de calor usando intensidad WiFi (RSSI) y coordenadas manuales.
// Protocolo serial compatible con Heat_live.py: DATA,x,y,valor

#include <WiFi.h>

const char* ssid = "R-Morisca-001";
const char* password = "A1b2C3d45E!";

const unsigned long WIFI_TIMEOUT_MS = 15000;
const unsigned long AUTO_INTERVAL_MS = 2000;

int currentX = 0;
int currentY = 0;
bool autoMode = true;
unsigned long lastMeasureMs = 0;

void setup() {
  Serial.begin(115200);
  delay(500);

  conectarWiFi();
  imprimirAyuda();
}

void loop() {
  leerComandoSerial();

  if (autoMode && millis() - lastMeasureMs >= AUTO_INTERVAL_MS) {
    medirYEnviar();
    lastMeasureMs = millis();
  }
}

void conectarWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  Serial.print("Conectando a WiFi");
  unsigned long startMs = millis();

  while (WiFi.status() != WL_CONNECTED && millis() - startMs < WIFI_TIMEOUT_MS) {
    delay(500);
    Serial.print(".");
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println();
    Serial.print("Conectado. IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println();
    Serial.println("No se pudo conectar al WiFi. Revisa SSID/password o senal.");
  }
}

void imprimirAyuda() {
  Serial.println("Comandos:");
  Serial.println("  X <num>   - fijar coordenada X");
  Serial.println("  Y <num>   - fijar coordenada Y");
  Serial.println("  M         - medir una vez");
  Serial.println("  A         - activar medicion automatica");
  Serial.println("  S         - detener medicion automatica");
  Serial.println("Emitiendo DATA automaticamente cada 2 segundos.");
}

void leerComandoSerial() {
  if (!Serial.available()) {
    return;
  }

  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  cmd.toUpperCase();

  if (cmd.length() == 0) {
    return;
  }

  if (cmd.startsWith("X")) {
    currentX = cmd.substring(1).toInt();
    Serial.print("X = ");
    Serial.println(currentX);
  } else if (cmd.startsWith("Y")) {
    currentY = cmd.substring(1).toInt();
    Serial.print("Y = ");
    Serial.println(currentY);
  } else if (cmd == "M") {
    medirYEnviar();
    lastMeasureMs = millis();
  } else if (cmd == "A") {
    autoMode = true;
    Serial.println("Modo automatico activado.");
  } else if (cmd == "S") {
    autoMode = false;
    Serial.println("Modo automatico detenido.");
  } else if (cmd == "H" || cmd == "?") {
    imprimirAyuda();
  } else {
    Serial.print("Comando no reconocido: ");
    Serial.println(cmd);
  }
}

void medirYEnviar() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WARN,WiFi desconectado");
    return;
  }

  int rssi = WiFi.RSSI();
  Serial.print("DATA,");
  Serial.print(currentX);
  Serial.print(",");
  Serial.print(currentY);
  Serial.print(",");
  Serial.println(rssi);
}
