#include <stdlib.h>
#include <string.h>
#include "cf_interpretation.h"

cf_status_t cf_b_interpret(
    const cf_assimilated_evidence_t* evidence,
    const cf_state_frame_t* state,
    const cf_tears_result_t* tears_associations,
    cf_interpretation_candidate_t** out_candidate
) {
    if (!evidence || !state || !out_candidate) return CF_INVALID_ARGUMENT;

    cf_interpretation_candidate_t* candidate = (cf_interpretation_candidate_t*)malloc(sizeof(cf_interpretation_candidate_t));
    if (!candidate) return CF_INTERNAL_ERROR;

    /* 
     * CÁLCULO DE HEAT LEGÍTIMO: 
     * La fricción sube si el estado de Lazzaro tiene estrés o si T.E.A.R.S. falló (degraded)
     */
    candidate->friction = state->stress * 0.6f;
    if (tears_associations && tears_associations->degraded) {
        candidate->friction += 0.4f; 
    }

    candidate->novelty = (state->curiosity > 0.5f) ? 0.8f : 0.2f;

    /* Asignación de postura dinámica */
    const char* default_stance = (candidate->friction > 0.7f) ? "resistant" : "curious";
    candidate->stance = (char*)malloc(strlen(default_stance) + 1);
    strcpy(candidate->stance, default_stance);

    const char* default_meaning = "Procesando input bajo marco actual";
    candidate->meaning = (char*)malloc(strlen(default_meaning) + 1);
    strcpy(candidate->meaning, default_meaning);

    /* Copia superficial del contexto asociativo para esta v0.1 */
    if (tears_associations) {
        candidate->associative_context = *tears_associations;
    } else {
        memset(&candidate->associative_context, 0, sizeof(cf_tears_result_t));
    }

    *out_candidate = candidate;
    return CF_OK;
}

void cf_interpretation_destroy(cf_interpretation_candidate_t* candidate) {
    if (candidate) {
        if (candidate->stance) free(candidate->stance);
        if (candidate->meaning) free(candidate->meaning);
        free(candidate);
    }
}
