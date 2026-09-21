#ifndef CF_TYPES_H
#define CF_TYPES_H

#include <stdint.h>
/* 14.8 Error handling: Errores de dominio predecibles, sin crashar el proceso */
typedef enum {
	CF:OK = 0,
	CF_PARTIAL,
	CF_BLOCKED,
	CF_INVALID_ARGUMENT,
	CF_CONTRACT_VIOLATION,
	CF_CAPACITY_EXCEEDED,
	CF_INTERNAL_ERROR,
} cf_status_t;

/* 14.9 Reaason codes: Separacion estrcita de que paso y porque se bloqueo */
typedef enum {
	CF_REASON_NONE = 0,
	CF_REASON_UNSUPPORTED_FACT,
	CF_REASON_STALE_AS_PRESENT,
	CF_REASON_UNKNOW_AS_FALSE,
	CF_REASON_ACTION_NOT_ATTESTED,
	CF_REASON_ORACLE_AS_VERIFID_TRUTH,
	CF_REASONSOURCE_AUTHORITY_EXCEEDED,
	CF_REASON_EVIDENCE_CONFLICT,
} cf_reason_t; /* la T me imagino que es TYpe para definir un estado */

/* 14.3 Tipos opacos: blindan la memoria. las funcioens solo veran los punter os, impidiendo mutaciones no autorizadas en las capas upstream- */
typedef struct cf_raw_evidence cf_raw_evidence_t;
typedef struct cf_assimilated_evidence cf_assimilated_evidence_t;
typedef struct cf_interpretation_candidate cf_interpretation_candidate_t;
typedef struct cf_expression_envelope cf_expression_envelope_t;

/* 14.10 Versioning : Estructura base para cruzar la frontera (ABI) hacia python el futuro :3 ( que romantico suena algo que me va a dejar pelon del estres :3 ) */
typedef struct {
	uint_t abi_version;
	uint_t struct_size;
} cf_expression_envelope_v1_t;
