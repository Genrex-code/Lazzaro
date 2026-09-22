#include <stdlib.h>
#include <string.h>
#include "cf_evidence.h"

/* 
 * Definición interna de la estructura opaca.
 * Al estar definida solo en el .c, ninguna otra capa puede mutar sus campos directamente.
 */
struct cf_raw_evidence {
    cf_provenance_t provenance;
    cf_payload_type_t payload_type;
    void* payload_data;
    size_t payload_length;
};

cf_status_t cf_evidence_create(
    const cf_provenance_t* prov,
    cf_payload_type_t payload_type,
    const void* payload_data,
    size_t payload_length,
    cf_raw_evidence_t** out_evidence) 
{
    if (!prov || !payload_data || !out_evidence || payload_length == 0) {
        return CF_INVALID_ARGUMENT;
    }

    /* Asignación de memoria para la estructura principal */
    cf_raw_evidence_t* evidence = (cf_raw_evidence_t*)malloc(sizeof(cf_raw_evidence_t));
    if (!evidence) {
        return CF_INTERNAL_ERROR;
    }

    /* Copia de la procedencia (Provenance) */
    evidence->provenance = *prov;
    evidence->payload_type = payload_type;
    evidence->payload_length = payload_length;

    /* CREACIÓN DEL SNAPSHOT: Se aísla el dato crudo copiándolo a un nuevo bloque de memoria */
    evidence->payload_data = malloc(payload_length);
    if (!evidence->payload_data) {
        free(evidence);
        return CF_INTERNAL_ERROR;
    }
    memcpy(evidence->payload_data, payload_data, payload_length);

    *out_evidence = evidence;
    return CF_OK;
}

cf_status_t cf_evidence_get_provenance(
    const cf_raw_evidence_t* evidence,
    const cf_provenance_t** out_prov) 
{
    if (!evidence || !out_prov) return CF_INVALID_ARGUMENT;
    
    *out_prov = &evidence->provenance;
    return CF_OK;
}

cf_status_t cf_evidence_get_payload(
    const cf_raw_evidence_t* evidence,
    const void** out_payload,
    size_t* out_length) 
{
    if (!evidence || !out_payload || !out_length) return CF_INVALID_ARGUMENT;

    *out_payload = evidence->payload_data;
    *out_length = evidence->payload_length;
    return CF_OK;
}

void cf_evidence_destroy(cf_raw_evidence_t* evidence) 
{
    if (evidence) {
        if (evidence->payload_data) {
            free(evidence->payload_data);
        }
        free(evidence);
    }
}
