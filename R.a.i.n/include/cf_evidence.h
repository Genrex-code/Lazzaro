#ifndef CF_EVIDENCE_H
#define CF_EVIDENCE_H

#include <stdint.h>
#include <stddef.h>
#include "cf_types.h" /* Incluye los tipos opacos y códigos de error base */

/* Clasificación de la fuente original (Provenance) */
typedef enum {
    CF_SOURCE_UNKNOWN = 0,
    CF_SOURCE_USER,
    CF_SOURCE_ORACLE,
    CF_SOURCE_MATERIAL,
    CF_SOURCE_SYSTEM
} cf_source_type_t;

/* Tipos de datos crudos (Strings, binarios, etc.) */
typedef enum {
    CF_DATA_STRING,
    CF_DATA_BINARY,
    CF_DATA_NUMERIC
} cf_payload_type_t;

/* 
 * Estructura de metadatos de procedencia.
 * Protege la identidad de la fuente, el timestamp y el ID de transacción.
 */
typedef struct {
    cf_source_type_t source;
    uint64_t timestamp_ms;
    const char* transaction_id;
} cf_provenance_t;

/* =====================================================================
   ZONA A: API DE SOLO LECTURA (CANONICAL EVIDENCE)
   ===================================================================== */

/* 
 * Instancia una nueva evidencia cruda. 
 * Crea un snapshot inmutable para evitar fugas de estado del runtime (TOCTOU).
 */
cf_status_t cf_evidence_create(
    const cf_provenance_t* prov,
    cf_payload_type_t payload_type,
    const void* payload_data,
    size_t payload_length,
    cf_raw_evidence_t** out_evidence
);

/* 
 * Recupera la procedencia de la evidencia.
 * El doble puntero const prohíbe modificaciones downstream.
 */
cf_status_t cf_evidence_get_provenance(
    const cf_raw_evidence_t* evidence,
    const cf_provenance_t** out_prov
);

/* 
 * Recupera el contenido crudo.
 * Solo lectura; la memoria sigue siendo propiedad de la Zona A.
 */
cf_status_t cf_evidence_get_payload(
    const cf_raw_evidence_t* evidence,
    const void** out_payload,
    size_t* out_length
);

/* Libera el snapshot de memoria al finalizar el ciclo de vida de la evidencia */
void cf_evidence_destroy(cf_raw_evidence_t* evidence);

#endif // CF_EVIDENCE_H
