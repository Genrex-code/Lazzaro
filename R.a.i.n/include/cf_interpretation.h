#ifndef CF_INTERPRETATION_H
#define CF_INTERPRETATION_H

#include "cf_types.h"
#include "cf_assimilation.h"

/* 
 * 14. StateFrame: El estado interno de R.A.I.N. (BOLOGIA/TCM).
 * INVARIANTE: Todos los valores deben estar normalizados (ej. [-1.0, 1.0]) 
 * para evitar singularidades en la métrica tensorial de TEARS.
 */
typedef struct {
    uint32_t frame_id;
    float stress;
    float energy;
    float curiosity;
    float pain;
    float novelty_bias;
} cf_subjective_state_v1_t;

/*
 * 16. TEARSResult (Mock para v0.1): 
 * Representa el vecindario asociativo devuelto por TEARS. 
 * "Estos elementos resultaron asociativamente relevantes, no son hechos verdaderos".
 */
typedef struct {
    uint32_t related_concept_id;
    float contextual_score; /* Qué tan fuerte es la asociación bajo el StateFrame actual */
} cf_associative_neighbor_t;

typedef struct {
    uint32_t index_epoch;
    cf_associative_neighbor_t neighbors[10]; /* Bounded array por seguridad en C */
    uint8_t neighbor_count;
} cf_association_view_v1_t;

/* 
 * 8.3 Estado Subjetivo Derivado (El Caos Bueno):
 * Lo que R.A.I.N. concluye internamente sobre el evento, mezclando 
 * la realidad, su estado biológico y sus asociaciones de memoria.
 */
struct cf_interpretation_candidate {
    float internal_friction;    /* Nivel de conflicto cognitivo */
    float perceived_salience;   /* Qué tan importante le parece a la entidad */
    uint32_t dominant_stance;   /* Ej. 1: Resistente, 2: Curioso, 3: Asustado */
    char* subjective_meaning;   /* Ej. "El usuario me está ignorando" (Puede estar equivocado) */
};

/* =====================================================================
   ZONA B: MOTOR DE INTERPRETACIÓN (DEVELOPMENTAL CORE)
   ===================================================================== */

/*
 * Genera un significado subjetivo.
 * LEY 4: Puede equivocarse en su interpretación sin corromper la evidencia canónica.
 * Los inputs de Zona A/T1 son estrictamente CONST.
 */
cf_status_t cf_b_interpret(
    const cf_assimilated_evidence_t* evidence,
    const cf_subjective_state_v1_t* current_state,
    const cf_association_view_v1_t* tears_associations,
    cf_interpretation_candidate_t** out_candidate
);

/* Limpieza de memoria del candidato interpretativo */
void cf_interpretation_destroy(cf_interpretation_candidate_t* candidate);

#endif // CF_INTERPRETATION_H
