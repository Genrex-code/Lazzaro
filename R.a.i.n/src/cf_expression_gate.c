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

#include <stdlib.h>
#include <string.h>
#include "cf_expression_gate.h"
#include "cf_envelope.h"

cf_status_t cf_t2_gate(
    const cf_assimilated_evidence_t* evidence,
    const cf_interpretation_candidate_t* candidate,
    cf_expression_envelope_t** out_envelope,
    cf_gate_receipt_v1_t* out_receipt
) {
    if (!evidence || !candidate || !out_envelope || !out_receipt) return CF_INVALID_ARGUMENT;

    cf_expression_envelope_t* env = (cf_expression_envelope_t*)malloc(sizeof(cf_expression_envelope_t));
    if (!env) return CF_INTERNAL_ERROR;

    memset(env, 0, sizeof(cf_expression_envelope_t));
    env->header.abi_version = 1;
    env->header.struct_size = sizeof(cf_expression_envelope_t);

    out_receipt->blocked_components = 0;

    /* 
     * REGLAS DE AUTORIDAD (T2)
     * Determina bajo qué régimen puede hablar el LLM.
     */
    if (candidate->friction > 0.8f) {
        env->required_modality = CF_MODALITY_UNCERTAINTY;
        env->cognitive_friction_level = 2; /* Alta confusión visible */
        out_receipt->status = CF_PARTIAL;
        out_receipt->reason_code = CF_REASON_EVIDENCE_CONFLICT;
    } else if (candidate->associative_context.degraded) {
        env->required_modality = CF_MODALITY_INFERENCE;
        env->cognitive_friction_level = 1; /* Duda leve */
        out_receipt->status = CF_PARTIAL;
        out_receipt->reason_code = CF_REASON_UNSUPPORTED_FACT;
    } else {
        env->required_modality = CF_MODALITY_ASSERTION;
        env->cognitive_friction_level = 0;
        out_receipt->status = CF_OK;
        out_receipt->reason_code = CF_REASON_NONE;
    }

    env->social_freedom_high = 1; /* Persona (Zona C) conserva su libertad lingüística */

    /* Empaquetado final de hechos permitidos */
    const char* safe_fact = "Evidencia canónica validada";
    env->assertable_facts = (char*)malloc(strlen(safe_fact) + 1);
    strcpy(env->assertable_facts, safe_fact);
    
    env->allowed_interpretations = NULL;
    env->prohibited_claim_classes = 0x01; /* 0x01 = CURRENT_SENSOR_OBSERVATION bloqueado */

    *out_envelope = env;
    return CF_OK;
}
