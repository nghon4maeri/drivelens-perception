# Research backing đã được dùng trong quá trình phân tích

**Status: RESEARCH EVIDENCE**

Mục tiêu file này không phải literature review đầy đủ. Nó chỉ ghi lại những paper đã trực tiếp ảnh hưởng tới cách hiểu bài toán.

## 1. Video summarization: Diversity không đủ

### Pang et al., WACV 2023
**Contrastive Losses Are Natural Criteria for Unsupervised Video Summarization**

Paper mô tả video summarization là chọn một subset frame informative và nêu ba đặc tính của keyframe:

- local dissimilarity;
- global consistency;
- uniqueness.

Điểm quan trọng với RAV-26:

> Chỉ chọn frame “khác frame lân cận” là chưa đủ. Một frame có thể khác cục bộ nhưng không đại diện tốt cho video hoặc chỉ là transient nuisance.

Nguồn:  
https://openaccess.thecvf.com/content/WACV2023/html/Pang_Contrastive_Losses_Are_Natural_Criteria_for_Unsupervised_Video_Summarization_WACV_2023_paper.html

## 2. Temporal change / boundary detection

### Du et al., CVPR 2022
**Fast and Unsupervised Action Boundary Detection for Action Segmentation**

Paper dùng similarity giữa các frame/features đã làm mượt để phát hiện change points/boundaries trong video.

Điểm liên quan:
- temporal similarity có thể xem như time-series signal;
- sustained change và transient spike là hai hiện tượng cần phân biệt;
- smoothing/windowing là hướng có cơ sở để khảo sát thay đổi theo thời gian.

Nguồn:  
https://openaccess.thecvf.com/content/CVPR2022/html/Du_Fast_and_Unsupervised_Action_Boundary_Detection_for_Action_Segmentation_CVPR_2022_paper.html

## 3. Semantic deduplication

### Abbas et al., 2023
**SemDeDup: Data-efficient learning at web-scale through semantic deduplication**

SemDeDup dùng pretrained embeddings để tìm semantic duplicates thay vì chỉ exact duplicates.

Điểm liên quan:

> Embeddings là công cụ hợp lý để phát hiện redundancy ở mức feature/semantic, nhưng kết quả trên web-scale image data không tự động chuyển thành claim cho ADAS video.

Nguồn:  
https://arxiv.org/abs/2303.09540

## 4. SearchAD

SearchAD được tách riêng ở `02_searchad_notes.md` vì paper này đã được thảo luận kỹ và dễ bị hiểu sai vai trò.

## Kết luận hẹp từ research backing

Các paper trên hỗ trợ ba nhận định:

1. Video selection là một bài toán đã có research base.
2. Diversity/local dissimilarity một mình không đủ để định nghĩa “good frame”.
3. Temporal structure và semantic redundancy là các tín hiệu có cơ sở nghiên cứu để khảo sát.

Chúng **không chứng minh** product-market fit, không chứng minh embedding diversity tốt hơn mọi baseline trên ADAS, và không chứng minh downstream training uplift.
