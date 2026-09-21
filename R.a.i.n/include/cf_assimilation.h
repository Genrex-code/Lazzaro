#ifndef CF_ASSIMILATION_H
#define CF_ASSIMILATION_H

#include "cf_types.h"
#include "cf_evidence.h"

/* 
 * 7.5 Receipt recomendado:
 * Registra exactamente cómo se transformó el dato al cruzar la membrana.
 * Si se perdió información (lossy), debe quedar explícito aquí.
 */
typedef struct {
    cf_source_type_t source_ref;
    uint32_t transform_kind; /* Ej. NORMALIZACION, PARSING_JSON, TYPE_CAST */
    cf_status_t status;
    uint8_t is_lossless;     /* 1 si la conversión fue perfecta, 0 si se truncaron datos */
    cf_reason_t reason_code;
} cf_assimilation_receipt_v1_t;

/* =====================================================================
   ZONA T1: MEMBRANA DE ASIMILACION (ASSIMILATION MEMBRANE)
   ===================================================================== */

/* 
 * 7.1 y 7.4 Regla de Autoridad: authority_out <= authority_in.
 * Traduce la evidencia cruda a una estructura asimilada.
 * LEY 2 APLICADA: El puntero 'input' es estrictamente 'const' para prohibir "backward writes".
 */
cf_status_t cf_t1_assimilate(
    const cf_raw_evidence_t* input,
    cf_assimilated_evidence_t** out_assimilated,
    cf_assimilation_receipt_v1_t* out_receipt
);

/* Limpieza de la memoria cuando el dato asimilado ya no se necesita en el turno */
void cf_assimilated_destroy(cf_assimilated_evidence_t* assimilated);

#endif // CF_ASSIMILATION_H
