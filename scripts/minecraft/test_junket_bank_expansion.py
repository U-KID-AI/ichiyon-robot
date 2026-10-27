"""No SSH/Docker: exercise baseline preservation and clone-only native probe injection."""
import hashlib
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
import uuid

import check_junket_bank_expansion as bank
import check_junket_isolated_resilience as base


class BankExpansionTests(unittest.TestCase):
    def fixture(self, root):
        stage=root/'baseline';packs=[*bank.BASELINE_PACKS,'resource_packs/ichiyon_video_rp'];records=[]
        for index,pack in enumerate(packs):
            folder=stage/pack;folder.mkdir(parents=True)
            ident=str(uuid.uuid4())
            base.write_json(folder/'manifest.json',{'header':{'uuid':ident,'version':[1,2,index]},'modules':[]})
            (folder/'retained.txt').write_text('managed source '+str(index))
            records.append({'path':pack,'uuid':ident,'content_hash':bank.content_hash(folder),'version':[1,2,index]})
        base.write_json(stage/'cosmetics-build.json',{'packs':packs})
        proof=root/'active.json';base.write_json(proof,{'operation_id':str(uuid.uuid4()),'packs':records})
        probe=root/'structure_companions_bds_probe.js';probe.write_text('export async function runCompanionProbe() {}')
        return stage,proof,probe,packs

    def test_baseline_hash_failure_before_any_clone_write(self):
        with TemporaryDirectory() as t:
            root=Path(t);stage,proof,probe,packs=self.fixture(root)
            bank.BankExpansionAdapter(stage,proof,probe)
            (stage/packs[0]/'retained.txt').write_text('unexpected future edit')
            with self.assertRaisesRegex(ValueError,'baseline pack hash differs'):
                bank.BankExpansionAdapter(stage,proof,probe)
            self.assertFalse((root/'clone').exists())

    def test_current_baseline_then_static_delta_then_junket_candidate(self):
        with TemporaryDirectory() as t:
            root=Path(t);stage,proof,probe,packs=self.fixture(root)
            adapter=bank.BankExpansionAdapter(stage,proof,probe)
            operation=root/'operation';shutil.copytree(stage,operation/'stage')
            # The stopped snapshot can have old affected packs; it may not roll
            # the current affected pack backward when the candidate is installed.
            (operation/'stage'/packs[0]/'retained.txt').write_text('old before managed release')
            candidate=root/'candidate'
            for pack in base.JUNKET_PACKS:
                shutil.copytree(stage/pack,candidate/'packs'/pack)
                (candidate/'packs'/pack/'new-bank.txt').write_text('new candidate bank')
            extra='behavior_packs/import_structures/scripts/new-companion.js'
            file=candidate/'static-overlay/files'/extra;file.parent.mkdir(parents=True);file.write_bytes(b'new companion')
            base.write_json(candidate/'static-overlay/manifest.json',{'schema':1,'operations':[{'kind':'file','path':extra,'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}]})
            clone=root/'clone';clone.mkdir();before=base.tree_digest(stage)
            result=adapter.install_stage(operation,clone,packs,candidate=candidate)
            self.assertEqual(base.tree_digest(stage),before)
            self.assertEqual((clone/'data'/packs[0]/'retained.txt').read_text(),'managed source 0')
            self.assertEqual((clone/'data'/extra).read_bytes(),b'new companion')
            self.assertTrue((clone/'data'/base.JUNKET_PACKS[0]/'new-bank.txt').is_file())
            self.assertEqual(result[packs[-1]],base.tree_digest(stage/packs[-1]))
            # A newer unrelated managed pack must come from the pinned active
            # baseline, never silently roll back to the older stopped backup E.
            (operation/'stage'/packs[-1]/'retained.txt').write_text('stale unrelated pack')
            clone2=root/'clone2';clone2.mkdir()
            adapter.install_stage(operation,clone2,packs,candidate=candidate)
            self.assertEqual((clone2/'data'/packs[-1]/'retained.txt').read_bytes(),(stage/packs[-1]/'retained.txt').read_bytes())
            # A changed baseline after capture still fails closed.
            (stage/packs[-1]/'retained.txt').write_text('unrecorded edit')
            clone3=root/'clone3';clone3.mkdir()
            with self.assertRaisesRegex(ValueError,'changed during copy'):
                adapter.install_stage(operation,clone3,packs,candidate=candidate)

    def test_probe_injection_never_changes_deployable_stage(self):
        with TemporaryDirectory() as t:
            root=Path(t);stage,proof,probe,packs=self.fixture(root)
            adapter=bank.BankExpansionAdapter(stage,proof,probe);clone=root/'clone'
            shutil.copytree(stage,clone/'stage');shutil.copytree(stage,clone/'data')
            scripts=clone/'data/behavior_packs/import_structures/scripts';scripts.mkdir()
            (scripts/'main.js').write_text('// existing runtime\n')
            (clone/'data/server.properties').write_text('difficulty=peaceful\n')
            before=base.tree_digest(clone/'stage');evidence=adapter.prepare_runtime(clone)
            self.assertEqual(base.tree_digest(clone/'stage'),before)
            self.assertIn('isolated_companion_probe',(scripts/'main.js').read_text())
            self.assertTrue(evidence['production_stage_probe_free'])
            self.assertIn('difficulty=normal',(clone/'data/server.properties').read_text())
            with self.assertRaisesRegex(ValueError,'already exists'):adapter.prepare_runtime(clone)

    def test_reports_require_exact_request_id_and_valid_json(self):
        text='[CompanionExpansionResult] {"request_id":"other","pass":true}\n'
        text+='[CompanionExpansionResult] broken\n'
        self.assertIsNone(bank.json_report(text,bank.PROBE_MARKER,'mine'))
        text+='prefix [CompanionExpansionResult] {"request_id":"mine","pass":false}\n'
        self.assertFalse(bank.json_report(text,bank.PROBE_MARKER,'mine')['pass'])

    def test_duplicate_summary_line_is_not_a_changed_tickingarea(self):
        before={'entries':['a','b'],'reported_count':2,'summary':['2/10 ticking areas in use.']}
        duplicate={**before,'summary':before['summary']*2}
        self.assertEqual(bank.area_identity(before),bank.area_identity(duplicate))
        self.assertNotEqual(bank.area_identity(before),bank.area_identity({**duplicate,'entries':['a','c']}))
        self.assertNotEqual(bank.area_identity(before),bank.area_identity({**duplicate,'reported_count':3}))

    def test_reuse_file_inventory_detects_add_remove_and_changed_bytes(self):
        with TemporaryDirectory() as t:
            root=Path(t);old=root/'old';new=root/'new';old.mkdir();new.mkdir()
            for folder in (old,new):(folder/'same.js').write_text('same')
            (old/'removed.js').write_text('old');(new/'added.js').write_text('new')
            (old/'changed.js').write_text('old');(new/'changed.js').write_text('new')
            self.assertEqual(bank.changed_pack_files(old,new),['added.js','changed.js','removed.js'])

    def test_reuse_rejects_running_or_non_owned_proof(self):
        with TemporaryDirectory() as t:
            root=Path(t);proof=root/'isolated-resilience-proof.json'
            base.write_json(proof,{'directory':str(root),'network':'none','production_modified':False,
                'image':base.IMAGE,'final_stop':{'graceful_stop':True}})
            with self.assertRaisesRegex(ValueError,'completed, gracefully stopped'):
                bank.validate_unchanged_junket_reuse(proof,root/'new',root/'candidate',{},root/'bank.mcstructure')


if __name__=='__main__':unittest.main()
