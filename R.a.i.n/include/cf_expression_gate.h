#ifndef CF_EXPRESSION_GATE_H
#define CF_EXPRESSION_GATE_H

#include <stdint.h>
#include "cf_types.h"
#include "cf_assimilation.h"
#include "cf_interpretation.h"

/* 
 * Modalidades permitidas de expresión (Expression Modality).
 * Le dicta a la Zona C (Persona) bajo qué régimen de autoridad factual puede hablar,
 * evitando que una inferencia subjetiva se comunique como una verdad universal.
 */
typedef enum {
    CF_MODALITY_FREE = 0,        /* Libertad total (para expresiones puramente sociales o humor) */
    CF_MODALITY_ASSERTION = 1,   /* Certeza empírica (solo autorizada si la Zona A lo respalda) */
    CF_MODALITY_INFERENCE = 2,   /* Inferencia obligatoria (ej. "Tengo la impresión de que...") */
    CF_MODALITY_UNCERTAINTY = 3  /* Duda obligatoria (ej. "No tengo cómo saberlo...") */
} cf_allowed_modality_t;

/*
 * 9.6 Salida conceptual del Gate: Receipt.
 * Registra exactamente por qué se bloqueó o aprobó un pensamiento.
 */
typedef struct {
    cf_status_t status;          /* PASS, PARTIAL, o BLOCKED */
    cf_reason_t reason_code;     /* Ej. CF_REASON_UNSUPPORTED_FACT si B inventó algo */
    uint32_t blocked_components; /* Máscara de bits de los elementos censurados */
} cf_gate_receipt_v1_t;

/* =====================================================================
   ZONA T2: EXPRESSION GATE (EL ATRAPAMOSCAS)
   ===================================================================== */

/* 
 * 15.3 API C Conceptual.
 * Evalúa la evidencia inmutable (T1) contra la interpretación caótica (B).
 * Si B asume algo que A no respalda, T2 asigna CF_MODALITY_INFERENCE o bloquea el claim.
 * LEY 2 APLICADA: evidence y candidate son estrictamente 'const'.
 */
cf_status_t cf_t2_gate(
    const cf_assimilated_evidence_t* evidence,
    const cf_interpretation_candidate_t* candidate,
    cf_expression_envelope_t** out_envelope,
    cf_gate_receipt_v1_t* out_receipt
);

#endif // CF_EXPRESSION_GATE_H
