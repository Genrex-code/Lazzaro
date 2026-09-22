#include <stdlib.h>
#include <string.h>
#include "cf_assimilation.h"
#include "cf_evidence.h"

/* 
 * Definición interna de la evidencia asimilada.
 * T1 transforma la representación (ej. normaliza strings, tipa IDs) pero NO el significado.
 */
struct cf_assimilated_evidence {
    cf_source_type_t original_source;
    uint64_t timestamp_ms;
    /* Para esta v0.1, asumimos que extrae y normaliza el texto/datos */
    char* normalized_content; 
    size_t content_length;
};

cf_status_t cf_t1_assimilate(
    const cf_raw_evidence_t* input,
    cf_assimilated_evidence_t** out_assimilated,
    cf_assimilation_receipt_v1_t* out_receipt)
{
    /* Validación de punteros nulos para no crashar el proceso (Invariante C) */
    if (!input || !out_assimilated || !out_receipt) {
        return CF_INVALID_ARGUMENT;
    }

    const cf_provenance_t* prov = NULL;
    const void* payload = NULL;
    size_t payload_len = 0;

    /* 
     * LEY 2 APLICADA (Const Correctness):
     * Leemos la Zona A mediante su API de solo lectura. Es imposible hacer "backward writes".
     */
    cf_status_t status = cf_evidence_get_provenance(input, &prov);
    if (status != CF_OK) return status;

    status = cf_evidence_get_payload(input, &payload, &payload_len);
    if (status != CF_OK) return status;

    /* Asignar memoria para la nueva estructura asimilada */
    cf_assimilated_evidence_t* assimilated = (cf_assimilated_evidence_t*)malloc(sizeof(cf_assimilated_evidence_t));
    if (!assimilated) {
        return CF_INTERNAL_ERROR;
    }

    /* Mapeo lossless de la procedencia */
    assimilated->original_source = prov->source;
    assimilated->timestamp_ms = prov->timestamp_ms;
    assimilated->content_length = payload_len;
    
    /* 
     * TRANSFORMACIÓN PERMITIDA (Membrana T1):
     * Aquí T1 parsearía JSON, normalizaría UTF-8, etc. 
     * Hacemos una copia limpia garantizando terminación nula para strings,
     * cumpliendo la prohibición de inyectar emociones, adjetivos o verdades nuevas.
     */
    assimilated->normalized_content = (char*)malloc(payload_len + 1);
    if (!assimilated->normalized_content) {
        free(assimilated);
        return CF_INTERNAL_ERROR;
    }
    memcpy(assimilated->normalized_content, payload, payload_len);
    assimilated->normalized_content[payload_len] = '\0'; /* Bounds safety (Invariante 9) */

    *out_assimilated = assimilated;

    /* Llenado estricto del recibo (Receipt) exigido por el contrato */
    out_receipt->source_ref = prov->source;
    out_receipt->transform_kind = 1; /* ID 1 = COPY_NORMALIZED temporalmente */
    out_receipt->status = CF_OK;
    out_receipt->is_lossless = 1;    /* Fue una copia estructural perfecta, sin pérdida */
    out_receipt->reason_code = CF_REASON_NONE;

    return CF_OK;
}

void cf_assimilated_destroy(cf_assimilated_evidence_t* assimilated) 
{
    /* Limpieza determinista para evitar fugas de memoria */
    if (assimilated) {
        if (assimilated->normalized_content) {
            free(assimilated->normalized_content);
        }
        free(assimilated);
    }
}
