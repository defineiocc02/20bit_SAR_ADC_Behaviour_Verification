// Causal 8-of-18 allocation. The bank that actually acquired the previous
// period is promoted to conversion. Select the next acquiring bank from its
// complement; two idle spares participate in service. A reproducible PRNG
// chooses the cyclic scan origin. This is not a uniform draw from all 8-of-18
// subsets, nor a claim to reproduce the unpublished silicon randomizer.
module slice_pool_ctrl #(parameter logic [31:0] P_SEED=32'h6d2b79f5) (
    input wire clk, rst_n, enable, advance, shuffle,
    output logic [7:0][4:0] acquiring_ids, converting_ids,
    output logic [17:0] acquiring_mask, converting_mask,
    output logic conversion_valid,
    output logic [31:0] sample_id
);
  logic primed;
  logic [31:0] random_state,random_next;
  initial if(P_SEED==0) $fatal(1,"slice_pool_ctrl: zero PRNG seed");
  always_comb begin
    random_next=random_state;
    for(int k=0;k<8;k++)
      random_next={1'b0,random_next[31:1]} ^ (random_next[0]?32'h80200003:32'd0);
  end
  logic [4:0] cursor;
  logic [7:0][4:0] next_ids;
  logic [17:0] next_mask;
  integer count, candidate;
  always_comb begin
    next_ids='0;next_mask='0;count=0;candidate=0;
    for(int k=0;k<18;k++) begin
      candidate=int'(cursor)+k;
      if(candidate>=18) candidate-=18;
      if(!acquiring_mask[candidate] && count<8) begin
        next_ids[count]=5'(candidate);next_mask[candidate]=1;count++;
      end
    end
  end
  always_ff @(posedge clk) begin
    if(!rst_n || !enable) begin
      for(int a=0;a<8;a++) begin acquiring_ids[a]<=5'(a);converting_ids[a]<='0;end
      acquiring_mask<=18'h000ff;converting_mask<='0;conversion_valid<=0;
      random_state<=P_SEED;cursor<=5'd8;primed<=0;sample_id<=0;
    end else if(advance) begin
      converting_ids<=acquiring_ids;converting_mask<=acquiring_mask;
      conversion_valid<=primed;primed<=1;sample_id<=sample_id+1'b1;
      acquiring_ids<=next_ids;acquiring_mask<=next_mask;
      if(shuffle) begin
        random_state<=random_next;
        if(random_state[31:24]<8'd252) cursor<=5'(random_state[31:24]%8'd18);
      end
      else cursor<=0;
    end
  end
endmodule
