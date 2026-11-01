# pow/: nodo de ejemplo de una cadena PoW

Un nodo HTTP de una mini-blockchain Proof of Work, en Python (FastAPI). Corre
como **un solo nodo**: guarda la cadena en JSON en `/data` (`DATA_DIR`), mina
con dificultad fija y expone `/healthz`, `/metrics`, `/chain`, `/tx` y `/mine`
en el puerto 8090.

```bash
make test-pow                       # Ejecutar para conocer el resultado actual
make build-pow                      # imagen pow:local
docker run -d --name pow -p 8090:8090 -v pow-data:/data pow:local
until curl -sf localhost:8090/healthz; do sleep 1; done; echo
curl -s -X POST localhost:8090/tx -H 'Content-Type: application/json' -d '{"sender":"ana","to":"beto","amount":1}'; echo
curl -s -X POST localhost:8090/mine | head -c 200; echo
curl -s localhost:8090/metrics | grep '^pow_block_height'
docker rm -f pow
```

En Kubernetes lo despliega `helm/charts/pow` (StatefulSet, un PVC por nodo y
un Service headless).

## Qué trae esta línea base y qué falta

El Trabajo Final es **tu propia blockchain PoW con varios nodos, en
producción en la nube**. `pow/` y `helm/charts/pow` no son esa blockchain:
son la línea base de la que partís.

| Ya está (no lo tenés que escribir) | Es tu TF (marcado `TODO(TF)` en el código) |
|---|---|
| `Block`, hash sha256, bloque génesis | dificultad ajustable |
| cadena en JSON en `/data`, reemplazo atómico y validación estructural | recepción y sincronización de cadenas entre peers |
| `POST /mine` con un loop de dificultad fija | peers: `GET /peers` y `POST /peers/sync` |
| `/healthz`, `/metrics` (`pow_block_height`), `/chain`, `/tx` en el puerto 8090 | consenso: la cadena más larga y válida gana |
| chart con StatefulSet, un PVC por nodo, Service headless y `PEERS` | persistencia del mempool, firmas y seguridad distribuida |

Los tests de `pow/tests/` que están en `skip` son la guía: cuando implementes
cada parte, sacales el `skip`. Con `replicaCount: 3` hoy tenés tres cadenas
**independientes**; que converjan es tu trabajo (al menos 2 nodos sincronizando).

## Cambios acotados del Lab 8

`validate_chain` comprueba estructura, génesis canónico, índices, enlaces,
recalculado de hashes y dificultad fija de tres ceros hexadecimales. No valida
saldos ni firmas y no selecciona una cadena ganadora.

`POST /mine` conserva `mined` y agrega `elapsed_seconds`, medido con reloj
monotónico exclusivamente alrededor del minado. Guarda el bloque antes de
vaciar las transacciones pendientes. Un lock ocupado devuelve 409; un estado
ilegible o un fallo de persistencia devuelve 503 sin reemplazarlo por génesis.
La validación básica de `/tx` rechaza nombres vacíos y montos no positivos o
no finitos con 422. Los endpoints de lectura y escritura comparten un lock
local; se debe mantener un solo proceso/worker por directorio de datos.

La escritura usa un temporal en el mismo directorio, `flush`, `fsync` y
`os.replace`. Esto evita exponer un JSON parcialmente escrito; no sustituye
backups ni acredita recuperación ante cortes de energía. El mempool sigue
en memoria. `/healthz` es salud del proceso, no una auditoría continua del disco.

El plan y las limitaciones del despliegue aislado están en
[`labs/clase8/README.md`](../labs/clase8/README.md). Los archivos de preparación
no acreditan un despliegue ni persistencia real sobre PVC.
