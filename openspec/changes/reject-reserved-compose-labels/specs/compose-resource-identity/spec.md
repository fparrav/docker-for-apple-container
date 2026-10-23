## ADDED Requirements

### Requirement: Reserved Compose labels are refused before anything is created
El namespace de etiquetas `com.docker.compose` (la clave `com.docker.compose` y toda clave que empiece por `com.docker.compose.`) pertenece al shim. `up` SHALL rechazar, antes de cualquier llamada que cree o modifique un recurso, toda etiqueta de usuario de ese namespace declarada en un servicio, una red o un volumen, en forma de mapa o de lista, nombrando la clave y el servicio o recurso. Las etiquetas que genera el shim y las etiquetas de usuario fuera del namespace SHALL seguir aplicándose.

#### Scenario: Reserved key on a service as a map
- **WHEN** el servicio `api` declara `labels: {com.docker.compose.project: another-project}`
- **THEN** `up` termina con error que nombra `com.docker.compose.project` y el servicio `api`, y el runtime no recibe ninguna llamada que cree o modifique una red, un volumen o un contenedor

#### Scenario: Reserved key on a service as a list
- **WHEN** el servicio `api` declara `labels: ["com.docker.compose.service=other"]`
- **THEN** `up` termina con el mismo tipo de error nombrando `com.docker.compose.service` y `api`, y no crea nada

#### Scenario: Reserved key on a network
- **WHEN** la red `back` declara una clave `com.docker.compose.*` en `labels`, en forma de mapa o de lista
- **THEN** `up` termina con error que nombra la clave y la red `back`, y no crea ninguna red, volumen ni contenedor

#### Scenario: Reserved key on a volume
- **WHEN** el volumen `data` declara una clave `com.docker.compose.*` en `labels`, en forma de mapa o de lista
- **THEN** `up` termina con error que nombra la clave y el volumen `data`, y no crea ninguna red, volumen ni contenedor

#### Scenario: Several offending labels are reported together
- **WHEN** un servicio y una red declaran cada uno una clave reservada
- **THEN** el error nombra ambas claves con su recurso, y no se crea nada

#### Scenario: Non-reserved labels keep working
- **WHEN** un servicio, una red y un volumen declaran etiquetas como `team=platform` o `com.example.owner=ops`
- **THEN** `up` crea los recursos y cada etiqueta aparece una sola vez en el recurso, junto con las etiquetas de propiedad del shim

#### Scenario: Internal labels are not refused
- **WHEN** `up` crea un servicio, una red y un volumen sin etiquetas de usuario
- **THEN** los recursos llevan las etiquetas `com.docker.compose.*` que genera el shim y `up` termina con éxito

#### Scenario: Same result with the official normalizer
- **WHEN** el modelo llega normalizado por el `docker-compose` oficial con una etiqueta reservada declarada en lista o en mapa
- **THEN** el resultado es el mismo error que con el parser integrado y no se crea nada

#### Scenario: Dry run reports the same problem
- **WHEN** `docker compose --dry-run up` se ejecuta con una etiqueta reservada
- **THEN** termina con el mismo error y el mismo código de salida que `up`, y no crea nada
