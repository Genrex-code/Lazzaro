#include <stdio.h>
#include <string.h>
#include "cf_evidence.h"
#include "cf_assimilation.h"
#include "cf_interpretation.h"
#include "cf_expression_gate.h"
#include "cf_envelope.h"

int main() {
    printf("--- INICIANDO SIMULACION R.A.I.N. ---\n\n");

    /* 1. ZONA A: Llega Evidencia Cruda (Ej. Un sensor falla) */
    cf_provenance_t prov = { CF_SOURCE_SYSTEM, 123456789, "tx_001" };
    const char* raw_payload = "Sensor_04_Timeout";
    
    cf_raw_evidence_t* evidence = NULL;
    cf_evidence_create(&prov, CF_DATA_STRING, raw_payload, strlen(raw_payload), &evidence);
    printf("[A] Evidencia cruda inmutable registrada.\n");

    /* 2. ZONA T1: Asimilación (Traducción limpia) */
    cf_assimilated_evidence_t* assimilated = NULL;
    cf_assimilation_receipt_v1_t receipt;
    cf_t1_assimilate(evidence, &assimilated, &receipt);
    printf("[T1] Dato asimilado. Lossless: %d, Reason: %d\n", receipt.is_lossless, receipt.reason_code);

    /* 3. ZONA B: Interpretación (Subjetividad / Lazzaro se estresa) */
    cf_state_frame_t state = { 1, 123456789, 0.9f, 0.5f, 0.2f, 0.0f, 0.1f }; // Estrés al 0.9!
    cf_tears_result_t tears_mock = { 100, NULL, 0, 1 }; // TEARS falla (degraded = 1)
    
    cf_interpretation_candidate_t* candidate = NULL;
    cf_b_interpret(assimilated, &state, &tears_mock, &candidate);
    printf("[B] Interpretacion generada. Friccion: %.2f, Postura: %s\n", candidate->friction, candidate->stance);

    /* 4. ZONA T2: Atrapamoscas (El Gate) */
    cf_expression_envelope_t* envelope = NULL;
    cf_gate_receipt_v1_t gate_receipt;
    cf_t2_gate(assimilated, candidate, &envelope, &gate_receipt);
    
    printf("[T2] Gate completado. Modalidad LLM permitida: %d\n", envelope->required_modality);
    if (envelope->required_modality == CF_MODALITY_UNCERTAINTY) {
        printf("[!] EL SISTEMA DETECTO ALTA FRICCION. EL LLM DEBE MOSTRAR DUDA.\n");
    }

    /* 5. ZONA C: Validación de salida (Envelope) */
    cf_status_t env_status = cf_expression_envelope_validate(envelope);
    if (env_status == CF_OK) {
        printf("[C] Sobre semantico validado y listo para exportar a Python.\n\n");
    }

    /* 6. LIMPIEZA DE MEMORIA (Como buen código en C) */
    cf_envelope_destroy(envelope);
    cf_interpretation_destroy(candidate);
    cf_assimilated_destroy(assimilated);
    cf_evidence_destroy(evidence);

    printf("--- SIMULACION TERMINADA SIN FUGAS DE MEMORIA ---\n");
    return 0;
}
