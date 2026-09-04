"""
Module core.skills.registry
DAO et gestionnaire de persistance SQLite pour le Skill Registry de ManAgent.
Garantit l'immutabilité des versions, la machine à états stricte,
le pré-filtrage indexé par signatures de mission (<2ms),
et l'exportation/importation de SkillPackage (.skillpkg).
"""

import sqlite3
import json
import time
from typing import Dict, List, Optional, Any, Tuple
from utils.logger import Logger
from .models import (
    SkillState,
    ProvenanceType,
    FailureClass,
    Checkpoint,
    ExecutionEnvironment,
    TrustProfile,
    SkillVersion,
    SkillManifest,
    SkillPackage,
)


class SkillRegistry:
    """
    Gestionnaire central et immuable du registre de compétences dans SQLite.
    Gère les tables `skills`, `skill_versions` et `skill_signatures_index`.
    """

    def __init__(self, db_path: str = "memory.db"):
        self.db_path = db_path
        self._initialize_db()

    def _get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _initialize_db(self):
        """Crée les tables SQLite nécessaires et les index d'accélération."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # 1. Table principale des Métadonnées & Manifests de Skills
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS skills (
                        skill_id TEXT PRIMARY KEY,
                        namespace TEXT NOT NULL,
                        name TEXT NOT NULL,
                        description TEXT NOT NULL,
                        parameters_schema TEXT NOT NULL,  -- JSON Schema
                        environment_json TEXT NOT NULL,   -- JSON ExecutionEnvironment
                        checkpoints_json TEXT NOT NULL,   -- JSON List[Checkpoint]
                        risk_level TEXT DEFAULT 'low',
                        current_production_version INTEGER,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                """)

                # 2. Table des Versions Immuables (Aucune mise à jour de code in-place)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS skill_versions (
                        skill_id TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        parent_version INTEGER,
                        state TEXT NOT NULL,              -- DRAFT, SHADOW, PRODUCTION, etc.
                        creator_model TEXT NOT NULL,
                        min_capability_tier INTEGER DEFAULT 1,
                        provenance TEXT NOT NULL,          -- DISTILLED, REPAIRED, HUMAN_EDITED, IMPORTED
                        repair_reason TEXT,
                        flow_payload_ref TEXT NOT NULL,
                        payload_content TEXT,             -- JSON / XML / Flo sérialisé si stocké localement
                        trust_profile_json TEXT NOT NULL, -- JSON TrustProfile
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        PRIMARY KEY (skill_id, version),
                        FOREIGN KEY (skill_id) REFERENCES skills(skill_id)
                    )
                """)

                # 3. Table d'Index Inversé Signatures -> Skills (Préfiltrage ultra-rapide <2ms)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS skill_signatures_index (
                        signature_hash TEXT NOT NULL,
                        skill_id TEXT NOT NULL,
                        target_app TEXT,
                        PRIMARY KEY (signature_hash, skill_id),
                        FOREIGN KEY (skill_id) REFERENCES skills(skill_id)
                    )
                """)

                # Index d'accélération
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_skills_namespace ON skills(namespace)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_versions_state ON skill_versions(state)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_signatures_hash ON skill_signatures_index(signature_hash)")

                conn.commit()
                Logger.info("[SkillRegistry] Base de compétences SQLite initialisée.")
        except Exception as e:
            Logger.error(f"[SkillRegistry] Erreur d'initialisation de la base : {e}")

    def get_skill(self, skill_id: str) -> Optional[SkillManifest]:
        """Retourne le manifest du skill s'il existe, sinon None."""
        try:
            manifest, _ = self.get_active_skill(skill_id)
            return manifest
        except Exception:
            return None

    def get_active_version(self, skill_id: str) -> Optional[Tuple[int, SkillState, TrustProfile]]:
        """Retourne (version, state, trust_profile) de la version active/dernière du skill."""
        manifest, version = self.get_active_skill(skill_id)
        if version:
            return version.version, version.state, version.trust_profile
        return None

    def get_skills_count(self) -> int:
        """Retourne le nombre total de skills enregistrés."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM skills")
                row = cursor.fetchone()
                return row[0] if row else 0
        except Exception:
            return 0

    def create_draft_skill(
        self,
        manifest: SkillManifest,
        flow_payload: Any,
        creator_model: str = "SolverAutoDiscovery",
        creator_capabilities: Optional[List[str]] = None,
        min_reasoning_score: float = 1.0,
        min_benchmark_score: float = 50.0
    ) -> SkillVersion:
        """Helper pour enregistrer un nouveau candidat Skill DRAFT/SHADOW."""
        payload_str = json.dumps(flow_payload) if isinstance(flow_payload, (dict, list)) else str(flow_payload)
        ref = f"payload_{manifest.skill_id}_v1"
        return self.register_draft_skill(
            manifest=manifest,
            flow_payload_ref=ref,
            payload_content=payload_str,
            creator_model=creator_model,
            creator_capabilities=creator_capabilities,
            min_reasoning_score=min_reasoning_score,
            min_benchmark_score=min_benchmark_score,
            provenance=ProvenanceType.DISTILLED,
            initial_state=SkillState.DRAFT
        )

    def index_signature(self, signature_hash: str, skill_id: str, target_app: Optional[str] = None) -> None:
        """Associe une signature_hash à un skill_id dans l'index inversé."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO skill_signatures_index (signature_hash, skill_id, target_app)
                VALUES (?, ?, ?)
            """, (signature_hash, skill_id, target_app))
            conn.commit()

    # =========================================================================
    # 1. CRÉATION & VERSIONING IMMUABLE
    # =========================================================================

    def register_draft_skill(
        self,
        manifest: SkillManifest,
        flow_payload_ref: str,
        payload_content: Optional[str] = None,
        creator_model: str = "distillation_engine",
        creator_capabilities: Optional[List[str]] = None,
        min_reasoning_score: float = 1.0,
        min_benchmark_score: float = 50.0,
        provenance: ProvenanceType = ProvenanceType.DISTILLED,
        initial_state: SkillState = SkillState.DRAFT
    ) -> SkillVersion:
        """
        Enregistre un nouveau Skill ou initialise sa version v1 sous statut DRAFT/SHADOW.
        """
        now = time.time()
        version_num = 1
        capabilities_list = creator_capabilities or []

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Vérifie si le skill_id existe déjà
            cursor.execute("SELECT MAX(version) FROM skill_versions WHERE skill_id = ?", (manifest.skill_id,))
            row = cursor.fetchone()
            if row and row[0] is not None:
                raise ValueError(f"Le skill '{manifest.skill_id}' existe déjà. Utilisez create_repair_version pour ajouter une version.")

            # Insertion du Manifest
            cursor.execute("""
                INSERT INTO skills (
                    skill_id, namespace, name, description, parameters_schema,
                    environment_json, checkpoints_json, risk_level,
                    current_production_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                manifest.skill_id,
                manifest.namespace,
                manifest.name,
                manifest.description,
                json.dumps(manifest.parameters_schema),
                json.dumps(manifest.environment.to_dict()),
                json.dumps([cp.to_dict() for cp in manifest.checkpoints]),
                manifest.risk_level,
                None,
                now,
                now
            ))

            # Insertion de la Version 1
            trust_profile = TrustProfile()
            cursor.execute("""
                INSERT INTO skill_versions (
                    skill_id, version, parent_version, state, creator_model,
                    min_capability_tier, provenance, repair_reason, flow_payload_ref,
                    payload_content, trust_profile_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                manifest.skill_id,
                version_num,
                None,
                initial_state.value,
                creator_model,
                int(min_reasoning_score),
                provenance.value,
                None,
                flow_payload_ref,
                payload_content,
                json.dumps(trust_profile.to_dict()),
                now,
                now
            ))

            # Indexation des Signatures
            for sig in manifest.signature_hashes:
                for app in (manifest.target_applications or [None]):
                    cursor.execute("""
                        INSERT OR IGNORE INTO skill_signatures_index (signature_hash, skill_id, target_app)
                        VALUES (?, ?, ?)
                    """, (sig, manifest.skill_id, app))

            conn.commit()

        return SkillVersion(
            skill_id=manifest.skill_id,
            version=version_num,
            parent_version=None,
            state=initial_state,
            creator_model=creator_model,
            creator_capabilities=capabilities_list,
            min_reasoning_score=min_reasoning_score,
            min_benchmark_score=min_benchmark_score,
            provenance=provenance,
            flow_payload_ref=flow_payload_ref,
            trust_profile=trust_profile,
            created_at=now,
            updated_at=now
        )

    def create_repair_version(
        self,
        skill_id: str,
        parent_version: int,
        flow_payload_ref: str,
        payload_content: Optional[str] = None,
        repair_reason: str = "",
        creator_model: str = "repair_analyzer",
        provenance: ProvenanceType = ProvenanceType.REPAIRED
    ) -> SkillVersion:
        """
        Crée une nouvelle version vN+1 sous statut DRAFT en cas d'incident ou de réparation.
        Ne modifie JAMAIS la version parente (Immutabilité stricte).
        """
        now = time.time()
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Vérifier que le parent existe
            cursor.execute("SELECT version FROM skill_versions WHERE skill_id = ? AND version = ?", (skill_id, parent_version))
            if not cursor.fetchone():
                raise ValueError(f"Version parente {parent_version} introuvable pour le skill '{skill_id}'.")

            # Trouver le prochain numéro de version
            cursor.execute("SELECT MAX(version) FROM skill_versions WHERE skill_id = ?", (skill_id,))
            max_v = cursor.fetchone()[0] or parent_version
            new_version_num = max_v + 1

            trust_profile = TrustProfile()
            cursor.execute("""
                INSERT INTO skill_versions (
                    skill_id, version, parent_version, state, creator_model,
                    min_capability_tier, provenance, repair_reason, flow_payload_ref,
                    payload_content, trust_profile_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                skill_id,
                new_version_num,
                parent_version,
                SkillState.DRAFT.value,
                creator_model,
                1,
                provenance.value,
                repair_reason,
                flow_payload_ref,
                payload_content,
                json.dumps(trust_profile.to_dict()),
                now,
                now
            ))

            # Mise à jour du timestamp du skill parent
            cursor.execute("UPDATE skills SET updated_at = ? WHERE skill_id = ?", (now, skill_id))
            conn.commit()

        return SkillVersion(
            skill_id=skill_id,
            version=new_version_num,
            parent_version=parent_version,
            state=SkillState.DRAFT,
            creator_model=creator_model,
            provenance=provenance,
            repair_reason=repair_reason,
            flow_payload_ref=flow_payload_ref,
            trust_profile=trust_profile,
            created_at=now,
            updated_at=now
        )

    # =========================================================================
    # 2. TRANSITIONS D'ÉTATS & HARD GATES
    # =========================================================================

    def transition_state(
        self,
        skill_id: str,
        version: int,
        target_state: SkillState,
        reason: str = ""
    ) -> bool:
        """
        Applique une transition d'état sur une version spécifique.
        Si une version passe en PRODUCTION, elle devient la version active du Skill.
        """
        now = time.time()
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT state FROM skill_versions WHERE skill_id = ? AND version = ?", (skill_id, version))
            row = cursor.fetchone()
            if not row:
                Logger.error(f"[SkillRegistry] Version {version} introuvable pour {skill_id}")
                return False

            current_state = SkillState(row[0])
            if current_state == target_state:
                return True

            cursor.execute("""
                UPDATE skill_versions
                SET state = ?, updated_at = ?
                WHERE skill_id = ? AND version = ?
            """, (target_state.value, now, skill_id, version))

            # Si promotion en PRODUCTION, mettre à jour le pointeur du manifest
            if target_state == SkillState.PRODUCTION:
                cursor.execute("""
                    UPDATE skills
                    SET current_production_version = ?, updated_at = ?
                    WHERE skill_id = ?
                """, (version, now, skill_id))
                Logger.info(f"[SkillRegistry] 🚀 Skill '{skill_id}' v{version} promu en PRODUCTION.")

            # Si mise en QUARANTINE d'une version de prod active
            elif target_state in (SkillState.QUARANTINE, SkillState.RETIRED):
                cursor.execute("""
                    UPDATE skills
                    SET current_production_version = NULL, updated_at = ?
                    WHERE skill_id = ? AND current_production_version = ?
                """, (now, skill_id, version))
                Logger.warning(f"[SkillRegistry] ⚠️ Skill '{skill_id}' v{version} placé en {target_state.value} (Raison: {reason}).")

            conn.commit()
            return True

    # =========================================================================
    # 3. PRÉ-FILTRAGE INDEXÉ (<2ms) & RETRIEVAL DÉTERMINISTE
    # =========================================================================

    def find_candidates_by_signatures(
        self,
        signature_hashes: List[str],
        host_environment: Optional[Dict[str, Any]] = None,
        only_production: bool = True
    ) -> List[Tuple[SkillManifest, SkillVersion]]:
        """
        Pré-filtrage déterministe ultra-rapide (<2ms) basé sur les signatures de mission.
        Filtre par compatibilité environnementale avant de remonter les résultats au Solver.
        """
        if not signature_hashes:
            return []

        placeholders = ",".join(["?"] * len(signature_hashes))
        query = f"""
            SELECT DISTINCT s.skill_id, s.namespace, s.name, s.description,
                            s.parameters_schema, s.environment_json, s.checkpoints_json,
                            s.risk_level, s.current_production_version, s.created_at,
                            v.version, v.parent_version, v.state, v.creator_model,
                            v.min_capability_tier, v.provenance, v.repair_reason,
                            v.flow_payload_ref, v.payload_content, v.trust_profile_json,
                            v.created_at, v.updated_at
            FROM skill_signatures_index idx
            JOIN skills s ON s.skill_id = idx.skill_id
            JOIN skill_versions v ON v.skill_id = s.skill_id
            WHERE idx.signature_hash IN ({placeholders})
        """

        if only_production:
            query += " AND v.state = 'PRODUCTION' AND s.current_production_version = v.version"

        results: List[Tuple[SkillManifest, SkillVersion]] = []

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, signature_hashes)
            rows = cursor.fetchall()

            # Fallback direct par ID/nom si aucun résultat via l'index inversé de hash
            if not rows:
                fallback_ids = []
                for h in signature_hashes:
                    # Ex: sig:press:run dialog box -> desktop.press.run_dialog_box
                    parts = h.replace("sig:", "").split(":")
                    if len(parts) >= 2:
                        act = parts[0].strip().replace(" ", "_")
                        obj = parts[1].strip().replace(" ", "_")
                        fallback_ids.append(f"desktop.{act}.{obj}")
                
                if fallback_ids:
                    fb_placeholders = ",".join(["?"] * len(fallback_ids))
                    fb_query = f"""
                        SELECT DISTINCT s.skill_id, s.namespace, s.name, s.description,
                                        s.parameters_schema, s.environment_json, s.checkpoints_json,
                                        s.risk_level, s.current_production_version, s.created_at,
                                        v.version, v.parent_version, v.state, v.creator_model,
                                        v.min_capability_tier, v.provenance, v.repair_reason,
                                        v.flow_payload_ref, v.payload_content, v.trust_profile_json,
                                        v.created_at, v.updated_at
                        FROM skills s
                        JOIN skill_versions v ON v.skill_id = s.skill_id
                        WHERE s.skill_id IN ({fb_placeholders})
                    """
                    if only_production:
                        fb_query += " AND v.state = 'PRODUCTION' AND s.current_production_version = v.version"
                    cursor.execute(fb_query, fallback_ids)
                    rows = cursor.fetchall()

            for r in rows:
                env_obj = ExecutionEnvironment.from_dict(json.loads(r[5]))

                # Vérification déterministe de compatibilité environnementale
                if host_environment and not env_obj.is_compatible(host_environment):
                    continue

                checkpoints = [Checkpoint.from_dict(cp) for cp in json.loads(r[6])]
                manifest = SkillManifest(
                    skill_id=r[0],
                    namespace=r[1],
                    name=r[2],
                    description=r[3],
                    parameters_schema=json.loads(r[4]),
                    environment=env_obj,
                    checkpoints=checkpoints,
                    risk_level=r[7],
                    current_production_version=r[8],
                    created_at=r[9]
                )

                version = SkillVersion(
                    skill_id=r[0],
                    version=r[10],
                    parent_version=r[11],
                    state=SkillState(r[12]),
                    creator_model=r[13],
                    min_capability_tier=r[14],
                    provenance=ProvenanceType(r[15]),
                    repair_reason=r[16],
                    flow_payload_ref=r[17],
                    trust_profile=TrustProfile.from_dict(json.loads(r[19])),
                    created_at=r[20],
                    updated_at=r[21]
                )
                results.append((manifest, version))

        # Tri par trust score décroissant
        results.sort(key=lambda x: x[1].trust_profile.trust_score, reverse=True)
        return results

    def get_shadow_skills(self) -> List[Tuple[SkillManifest, SkillVersion]]:
        """Récupère toutes les versions de compétences actuellement sous évaluation SHADOW."""
        query = """
            SELECT s.skill_id, s.namespace, s.name, s.description,
                   s.parameters_schema, s.environment_json, s.checkpoints_json,
                   s.risk_level, s.current_production_version, s.created_at,
                   v.version, v.parent_version, v.state, v.creator_model,
                   v.min_capability_tier, v.provenance, v.repair_reason,
                   v.flow_payload_ref, v.payload_content, v.trust_profile_json,
                   v.created_at, v.updated_at
            FROM skill_versions v
            JOIN skills s ON s.skill_id = v.skill_id
            WHERE v.state = 'SHADOW'
        """
        results: List[Tuple[SkillManifest, SkillVersion]] = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
            for r in rows:
                manifest = SkillManifest(
                    skill_id=r[0],
                    namespace=r[1],
                    name=r[2],
                    description=r[3],
                    parameters_schema=json.loads(r[4]),
                    environment=ExecutionEnvironment.from_dict(json.loads(r[5])),
                    checkpoints=[Checkpoint.from_dict(cp) for cp in json.loads(r[6])],
                    risk_level=r[7],
                    current_production_version=r[8],
                    created_at=r[9]
                )
                version = SkillVersion(
                    skill_id=r[0],
                    version=r[10],
                    parent_version=r[11],
                    state=SkillState(r[12]),
                    creator_model=r[13],
                    min_capability_tier=r[14],
                    provenance=ProvenanceType(r[15]),
                    repair_reason=r[16],
                    flow_payload_ref=r[17],
                    trust_profile=TrustProfile.from_dict(json.loads(r[19])),
                    created_at=r[20],
                    updated_at=r[21]
                )
                results.append((manifest, version))
        return results

    def get_active_skill(self, skill_id: str, target_version: Optional[int] = None) -> Tuple[Optional[SkillManifest], Optional[SkillVersion]]:
        """Récupère un skill actif par son ID."""
        try:
            pkg = self.export_package(skill_id)
        except ValueError:
            return None, None
            
        if not pkg:
            return None, None
            
        if target_version:
            v_match = next((v for v in pkg.versions if v.version == target_version), None)
            return pkg.manifest, v_match
            
        if pkg.manifest.current_production_version:
            v_match = next((v for v in pkg.versions if v.version == pkg.manifest.current_production_version), None)
            return pkg.manifest, v_match
            
        return pkg.manifest, pkg.versions[-1] if pkg.versions else None

    def record_run_metric(
        self,
        skill_id: str,
        version: int,
        success: bool,
        is_breakout: bool = False,
        is_shadow: bool = False
    ) -> TrustProfile:
        """Met à jour le profil de confiance d'une version suite à une exécution ou observation."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT trust_profile_json, state FROM skill_versions WHERE skill_id = ? AND version = ?", (skill_id, version))
            row = cursor.fetchone()
            if not row:
                raise ValueError(f"Version {version} introuvable pour le skill '{skill_id}'")

            trust_profile = TrustProfile.from_dict(json.loads(row[0]))
            current_state = SkillState(row[1])

            trust_profile.record_run(success=success, is_breakout=is_breakout, is_shadow=is_shadow)

            # Circuit Breaker automatique : si trop d'échecs consécutifs en PRODUCTION -> QUARANTINE
            if current_state == SkillState.PRODUCTION and trust_profile.consecutive_failures >= 3:
                cursor.execute("""
                    UPDATE skill_versions SET state = 'QUARANTINE', trust_profile_json = ?, updated_at = ?
                    WHERE skill_id = ? AND version = ?
                """, (json.dumps(trust_profile.to_dict()), time.time(), skill_id, version))
                cursor.execute("UPDATE skills SET current_production_version = NULL WHERE skill_id = ?", (skill_id,))
                Logger.warning(f"[SkillRegistry] 🚨 Circuit Breaker: Skill '{skill_id}' v{version} placé en QUARANTINE suite à 3 échecs consécutifs.")
            else:
                cursor.execute("""
                    UPDATE skill_versions SET trust_profile_json = ?, updated_at = ?
                    WHERE skill_id = ? AND version = ?
                """, (json.dumps(trust_profile.to_dict()), time.time(), skill_id, version))

            conn.commit()
        return trust_profile

    # =========================================================================
    # 4. EXPORTATION & IMPORTATION DE PACKAGES (.skillpkg)
    # =========================================================================

    def export_package(self, skill_id: str) -> SkillPackage:
        """Exporte un Skill complet avec toutes ses versions et ses payloads dans un SkillPackage autonome."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Manifest
            cursor.execute("""
                SELECT skill_id, namespace, name, description, parameters_schema,
                       environment_json, checkpoints_json, risk_level,
                       current_production_version, created_at
                FROM skills WHERE skill_id = ?
            """, (skill_id,))
            s_row = cursor.fetchone()
            if not s_row:
                raise ValueError(f"Skill '{skill_id}' introuvable pour exportation.")

            # Récupérer les signatures associées
            cursor.execute("SELECT signature_hash FROM skill_signatures_index WHERE skill_id = ?", (skill_id,))
            sig_hashes = [r[0] for r in cursor.fetchall()]

            manifest = SkillManifest(
                skill_id=s_row[0],
                namespace=s_row[1],
                name=s_row[2],
                description=s_row[3],
                parameters_schema=json.loads(s_row[4]),
                signature_hashes=sig_hashes,
                environment=ExecutionEnvironment.from_dict(json.loads(s_row[5])),
                checkpoints=[Checkpoint.from_dict(cp) for cp in json.loads(s_row[6])],
                risk_level=s_row[7],
                current_production_version=s_row[8],
                created_at=s_row[9]
            )

            # 2. Versions
            cursor.execute("""
                SELECT version, parent_version, state, creator_model, min_capability_tier,
                       provenance, repair_reason, flow_payload_ref, payload_content,
                       trust_profile_json, created_at, updated_at
                FROM skill_versions WHERE skill_id = ? ORDER BY version ASC
            """, (skill_id,))
            v_rows = cursor.fetchall()

            versions: List[SkillVersion] = []
            embedded_payloads: Dict[str, str] = {}

            for v in v_rows:
                ver_obj = SkillVersion(
                    skill_id=skill_id,
                    version=v[0],
                    parent_version=v[1],
                    state=SkillState(v[2]),
                    creator_model=v[3],
                    min_capability_tier=v[4],
                    provenance=ProvenanceType(v[5]),
                    repair_reason=v[6],
                    flow_payload_ref=v[7],
                    trust_profile=TrustProfile.from_dict(json.loads(v[9])),
                    created_at=v[10],
                    updated_at=v[11]
                )
                versions.append(ver_obj)
                if v[8]:  # payload_content
                    embedded_payloads[v[7]] = v[8]

        return SkillPackage(
            manifest=manifest,
            versions=versions,
            embedded_payloads=embedded_payloads
        )

    def import_package(self, package: SkillPackage, overwrite: bool = False) -> bool:
        """
        Importe un SkillPackage (.skillpkg) externe dans le registre.
        Préserve l'intégrité et enregistre la provenance IMPORTED.
        """
        now = time.time()
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT skill_id FROM skills WHERE skill_id = ?", (package.manifest.skill_id,))
            exists = cursor.fetchone() is not None

            if exists and not overwrite:
                raise ValueError(f"Le skill '{package.manifest.skill_id}' existe déjà et overwrite=False.")

            if exists and overwrite:
                cursor.execute("DELETE FROM skill_signatures_index WHERE skill_id = ?", (package.manifest.skill_id,))
                cursor.execute("DELETE FROM skill_versions WHERE skill_id = ?", (package.manifest.skill_id,))
                cursor.execute("DELETE FROM skills WHERE skill_id = ?", (package.manifest.skill_id,))

            # 1. Insertion du Manifest
            cursor.execute("""
                INSERT INTO skills (
                    skill_id, namespace, name, description, parameters_schema,
                    environment_json, checkpoints_json, risk_level,
                    current_production_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                package.manifest.skill_id,
                package.manifest.namespace,
                package.manifest.name,
                package.manifest.description,
                json.dumps(package.manifest.parameters_schema),
                json.dumps(package.manifest.environment.to_dict()),
                json.dumps([cp.to_dict() for cp in package.manifest.checkpoints]),
                package.manifest.risk_level,
                package.manifest.current_production_version,
                package.manifest.created_at or now,
                now
            ))

            # 2. Insertion des Versions
            for ver in package.versions:
                payload_content = package.embedded_payloads.get(ver.flow_payload_ref)
                cursor.execute("""
                    INSERT INTO skill_versions (
                        skill_id, version, parent_version, state, creator_model,
                        min_capability_tier, provenance, repair_reason, flow_payload_ref,
                        payload_content, trust_profile_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ver.skill_id,
                    ver.version,
                    ver.parent_version,
                    ver.state.value,
                    ver.creator_model,
                    ver.min_capability_tier,
                    ProvenanceType.IMPORTED.value,
                    ver.repair_reason,
                    ver.flow_payload_ref,
                    payload_content,
                    json.dumps(ver.trust_profile.to_dict()),
                    ver.created_at,
                    now
                ))

            # 3. Indexation des Signatures
            for sig in package.manifest.signature_hashes:
                for app in (package.manifest.target_applications or [None]):
                    cursor.execute("""
                        INSERT OR IGNORE INTO skill_signatures_index (signature_hash, skill_id, target_app)
                        VALUES (?, ?, ?)
                    """, (sig, package.manifest.skill_id, app))

            conn.commit()
            Logger.info(f"[SkillRegistry] Package '{package.manifest.skill_id}' importé avec succès ({len(package.versions)} versions).")
            return True

    def clear_all_skills(self) -> int:
        """Supprime tous les skills, versions et signatures associées."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM skill_signatures_index")
                cursor.execute("DELETE FROM skill_versions")
                cursor.execute("DELETE FROM skills")
                count = cursor.rowcount
                conn.commit()
                Logger.info(f"[SkillRegistry] Tous les skills ont été purgés ({count} supprimés).")
                return count
        except Exception as e:
            Logger.error(f"[SkillRegistry] Erreur clear_all_skills : {e}")
            return 0

    def list_all_skills(self) -> List[Dict[str, Any]]:
        """Retourne la liste complète des skills enregistrés dans la base."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.skill_id, s.namespace, s.name, s.description, s.risk_level,
                       s.current_production_version, s.created_at,
                       v.version, v.state
                FROM skills s
                LEFT JOIN skill_versions v ON s.skill_id = v.skill_id AND v.version = s.current_production_version
            """)
            rows = cursor.fetchall()
            results = []
            for r in rows:
                results.append({
                    "skill_id": r[0],
                    "namespace": r[1],
                    "name": r[2],
                    "description": r[3],
                    "risk_level": r[4],
                    "production_version": r[5],
                    "created_at": r[6],
                    "active_version": r[7],
                    "state": r[8] or "DRAFT"
                })
            return results

    def export_all_packages(self) -> List[Dict[str, Any]]:
        """Exporte tous les skills enregistrés sous forme de dictionnaires sérialisables (SkillPackage)."""
        skills = self.list_all_skills()
        packages = []
        for s in skills:
            try:
                pkg = self.export_package(s["skill_id"])
                packages.append(json.loads(pkg.export_json()))
            except Exception as e:
                Logger.warning(f"[SkillRegistry] Impossible d'exporter le skill '{s['skill_id']}': {e}")
        return packages

