/*
  Sketch basico de adquisicion para el nodo fisico Arduino.

  Este programa inicializa el sensor DHT22 y publica por el puerto serie las
  lecturas de temperatura y humedad. Dentro del TFG, este sketch representa la
  fuente fisica de datos que luego puede ser leida por Python para:

  - guardar lecturas en local,
  - enviarlas a la API web,
  - o ambas cosas a la vez.

  Flujo general del sketch:
  1. Arrancar el puerto serie.
  2. Inicializar el sensor DHT22.
  3. Leer temperatura y humedad en cada ciclo.
  4. Enviar los resultados por Serial en un formato legible.
  5. Esperar unos segundos antes de repetir.
*/

#include <DHT.h>

// Pin digital donde se conecta la linea de datos del DHT22.
#define DHTPIN 7

// Tipo de sensor ambiental utilizado en la maqueta.
#define DHTTYPE DHT22

// Instancia principal de la libreria DHT.
DHT dht(DHTPIN, DHTTYPE);

void setup() {
  // Se abre el puerto serie para monitorizacion y lectura desde Python.
  Serial.begin(9600);

  // Mensaje inicial util para comprobar que el sketch ha arrancado bien.
  Serial.println("Iniciando DHT22...");

  // Inicializa la libreria y el sensor fisico.
  dht.begin();

  // Pequena espera para estabilizar la primera lectura.
  delay(3000);
}

void loop() {
  // Lee las dos magnitudes ambientales soportadas por este sensor.
  float humedad = dht.readHumidity();
  float temperatura = dht.readTemperature();

  // Si el sensor falla, se informa y se reintenta en el siguiente ciclo.
  if (isnan(humedad) || isnan(temperatura)) {
    Serial.println("Error leyendo DHT22");
    delay(2000);
    return;
  }

  // Publicamos la temperatura con un prefijo claro para facilitar el parseo.
  Serial.print("Temperatura: ");
  Serial.print(temperatura);
  Serial.print(" C  ");

  // Publicamos la humedad en la misma linea para lectura humana rapida.
  Serial.print("Humedad: ");
  Serial.print(humedad);
  Serial.println(" %");

  // Cadencia basica de muestreo del nodo fisico.
  delay(3000);
}
